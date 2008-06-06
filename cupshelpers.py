## system-config-printer

## Copyright (C) 2006, 2007, 2008 Red Hat, Inc.
## Copyright (C) 2006 Florian Festi <ffesti@redhat.com>
## Copyright (C) 2006, 2007, 2008 Tim Waugh <twaugh@redhat.com>

## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

import cups, pprint, os, tempfile, re
from rhpl.translate import _, N_
import locale

def debugprint(x):
    try:
        print x
    except:
        pass

class Printer:

    printer_states = { cups.IPP_PRINTER_IDLE: _("Idle"),
                       cups.IPP_PRINTER_PROCESSING: _("Processing"),
                       cups.IPP_PRINTER_BUSY: _("Busy"),
                       cups.IPP_PRINTER_STOPPED: _("Stopped") }

    def __init__(self, name, connection, **kw):
        self.name = name
        self.connection = connection
        self.class_members = []
        self.device_uri = kw.get('device-uri', "")
        self.info = kw.get('printer-info', "")
        self.is_shared = kw.get('printer-is-shared', None)
        self.location = kw.get('printer-location', "")
        self.make_and_model = kw.get('printer-make-and-model', "")
        self.state = kw.get('printer-state', 0)
        self.type = kw.get('printer-type', 0)
        self.uri_supported = kw.get('printer-uri-supported', "")
        if type (self.uri_supported) == list:
            self.uri_supported = self.uri_supported[0]
        self._expand_flags()

        self.state_description = self.printer_states.get(
            self.state, _("Unknown"))

        self.enabled = self.state != cups.IPP_PRINTER_STOPPED

        if self.is_shared is None:
            self.is_shared = not self.not_shared
        del self.not_shared

        if self.is_class:
            self._ppd = False
        else:
            self._ppd = None # load on demand

    _flags_blacklist = ["options", "local"]

    def _expand_flags(self):
        prefix = "CUPS_PRINTER_"
        prefix_length = len(prefix)
        # loop over cups constants
        for name in cups.__dict__:
            if name.startswith(prefix):
                attr_name = name[prefix_length:].lower()
                if attr_name in self._flags_blacklist: continue
                if attr_name == "class": attr_name = "is_class"
                # set as attribute
                setattr(self, attr_name,
                        bool(self.type & getattr(cups, name)))

    def getAttributes(self):
        attrs = self.connection.getPrinterAttributes(self.name)
        self.attributes = {}
        self.other_attributes = {}
        self.possible_attributes = {
            'landscape' : ('False', ['True', 'False']),
            'page-border' : ('none', ['none', 'single', 'single-thick',
                                     'double', 'double-thick']),
            }

        for key, value in attrs.iteritems():
            if key.endswith("-default"):
                name = key[:-len("-default")]
                if name in ["job-sheets", "printer-error-policy",
                            "printer-op-policy", # handled below
                            "notify-events", # cannot be set
                            "document-format", # cannot be set
                            "notify-lease-duration"]: # cannot be set
                    continue 

                supported = attrs.get(name + "-supported", None) or \
                            self.possible_attributes.get(name, None) or \
                            ""

                # Convert a list into a comma-separated string, since
                # it can only really have been misinterpreted as a list
                # by CUPS.
                if isinstance (value, list):
                    value = reduce (lambda x, y: x+','+y, value)

                self.attributes[name] = value
                    
                if attrs.has_key(name+"-supported"):
                    self.possible_attributes[name] = (
                        value, attrs[name+"-supported"]) 
            elif (not key.endswith ("-supported") and
                  key != 'job-sheets-default' and
                  key != 'printer-error-policy' and
                  key != 'printer-op-policy' and
                  not key.startswith ('requesting-user-name-')):
                self.other_attributes[key] = value
        
        self.job_sheet_start, self.job_sheet_end = attrs.get(
            'job-sheets-default', ('none', 'none'))
        self.job_sheets_supported = attrs.get('job-sheets-supported', ['none'])
        self.error_policy = attrs.get('printer-error-policy', 'none')
        self.error_policy_supported = attrs.get(
            'printer-error-policy-supported', ['none'])
        self.op_policy = attrs.get('printer-op-policy', "") or "default"
        self.op_policy_supported = attrs.get(
            'printer-op-policy-supported', ["default"])

        self.default_allow = True
        self.except_users = []
        if attrs.has_key('requesting-user-name-allowed'):
            self.except_users = attrs['requesting-user-name-allowed']
            self.default_allow = False
        elif attrs.has_key('requesting-user-name-denied'):
            self.except_users = attrs['requesting-user-name-denied']
        self.except_users_string = ', '.join(self.except_users)

    def getServer(self):
        """return Server URI or None"""
        if not self.uri_supported.startswith('ipp://'):
            return None
        uri = self.uri_supported[6:]
        uri = uri.split('/')[0]
        uri = uri.split(':')[0]
        if uri == "localhost.localdomain":
            uri = "localhost"
        return uri

    def getPPD(self):
        """
        return cups.PPD object or False for raw queues
        raise cups.IPPError
        """
        if self._ppd is None:
            try:
                filename = self.connection.getPPD(self.name)
                self._ppd = cups.PPD(filename)
                os.unlink(filename)
            except cups.IPPError, (e, m):
                if e == cups.IPP_NOT_FOUND:
                    self._ppd = False
                else:
                    raise
        return self._ppd

    def setOption(self, name, value):
        if isinstance (value, float):
            radixchar = locale.nl_langinfo (locale.RADIXCHAR)
            if radixchar != '.':
                # Convert floats to strings, being careful with decimal points.
                value = str (value).replace (radixchar, '.')
        self.connection.addPrinterOptionDefault(self.name, name, value)

    def unsetOption(self, name):
        self.connection.deletePrinterOptionDefault(self.name, name)

    def setEnabled(self, on, reason=None):
        if on:
            self.connection.enablePrinter(self.name)
        else:
            if reason:
                self.connection.disablePrinter(self.name, reason=reason)
            else:
                self.connection.disablePrinter(self.name)

    def setAccepting(self, on, reason=None):
        if on:
            self.connection.acceptJobs(self.name)
        else:
            if reason:
                self.connection.rejectJobs(self.name, reason=reason)
            else:
                self.connection.rejectJobs(self.name)

    def setShared(self,on):
        self.connection.setPrinterShared(self.name, on)

    def setErrorPolicy (self, policy):
        self.connection.setPrinterErrorPolicy(self.name, policy)

    def setOperationPolicy(self, policy):
        self.connection.setPrinterOpPolicy(self.name, policy)    

    def setJobSheets(self, start, end):
        self.connection.setPrinterJobSheets(self.name, start, end)

    def setAccess(self, allow, except_users):
        if isinstance(except_users, str):
            users = except_users.split()
            users = [u.split(",") for u in users]
            except_users = []
            for u in users:
                except_users.extend(u)
            except_users = [u.strip() for u in except_users]
            except_users = filter(None, except_users)
            
        if allow:
            self.connection.setPrinterUsersDenied(self.name, except_users)
        else:
            self.connection.setPrinterUsersAllowed(self.name, except_users)

    def testsQueued(self):
        """Returns a list of job IDs for test pages in the queue for this
        printer."""
        ret = []
        try:
            jobs = self.connection.getJobs ()
        except cups.IPPError:
            return ret

        for id, attrs in jobs.iteritems():
            try:
                uri = attrs['job-printer-uri']
                uri = uri[uri.rindex ('/') + 1:]
            except:
                continue
            if uri != self.name:
                continue

            if attrs.has_key ('job-name') and attrs['job-name'] == 'Test Page':
                ret.append (id)
        return ret

def getPrinters(connection):
    printers = connection.getPrinters()
    classes = connection.getClasses()
    printers_conf = None
    for name, printer in printers.iteritems():
        printer = Printer(name, connection, **printer)
        printers[name] = printer
        if classes.has_key(name):
            printer.class_members = classes[name]
            printer.class_members.sort()

        if printer.device_uri.startswith ("smb:"):
            # smb: URIs may have been sanitized (authentication details
            # removed), so fetch the actual details from printers.conf.
            if not printers_conf:
                printers_conf = PrintersConf(connection)
            if printers_conf.device_uris.has_key(name):
                printer.device_uri = printers_conf.device_uris[name]
        if not printer.__dict__.has_key ('discovered'):
            # The CUPS_PRINTER_DISCOVERED flag is new in pycups-1.9.37.
            printer.discovered = False
            if printer.device_uri.startswith ("ipp:"):
                # ipp: Queues can be automatically created as a reaction
                # to a queue broadcasted by a remote CUPS server. These
                # queues are not listed in printers,conf. Mark them so
                # that we can decide which queue entries in the main window
                # should be editable/deletable and which not
                if not printers_conf:
                    printers_conf = PrintersConf(connection)
                if not printers_conf.device_uris.has_key(name):
                    printer.discovered = True
    return printers

def parseDeviceID (id):
    id_dict = {}
    pieces = id.split(";")
    for piece in pieces:
        if piece.find(":") == -1:
            continue
        name, value = piece.split(":",1)
        id_dict[name] = value
    if id_dict.has_key ("MANUFACTURER"):
        id_dict.setdefault("MFG", id_dict["MANUFACTURER"])
    if id_dict.has_key ("MODEL"):
        id_dict.setdefault("MDL", id_dict["MODEL"])
    if id_dict.has_key ("COMMAND SET"):
        id_dict.setdefault("CMD", id_dict["COMMAND SET"])
    for name in ["MFG", "MDL", "CMD", "CLS", "DES", "SN", "S", "P", "J"]:
        id_dict.setdefault(name, "")
    id_dict["CMD"] = id_dict["CMD"].split(',') 
    return id_dict

class Device:

    prototypes = {
        'ipp' : "ipp://%s"
        }

    def __init__(self, uri, **kw):
        self.uri = uri
        self.device_class = kw.get('device-class', 'Unknown') # XXX better default
        self.info = kw.get('device-info', '')
        self.make_and_model = kw.get('device-make-and-model', 'Unknown')
        self.id = kw.get('device-id', '')

        uri_pieces = uri.split(":")
        self.type =  uri_pieces[0]
        self.is_class = len(uri_pieces)==1

        #self.id = 'MFG:HEWLETT-PACKARD;MDL:DESKJET 990C;CMD:MLC,PCL,PML;CLS:PRINTER;DES:Hewlett-Packard DeskJet 990C;SN:US05N1J00XLG;S:00808880800010032C1000000C2000000;P:0800,FL,B0;J:                    ;'

        self.id_dict = parseDeviceID (self.id)

    def __cmp__(self, other):
        if self.is_class != other.is_class:
            if other.is_class:
                return -1
            return 1
        if not self.is_class and (self.type != other.type):
            # "hp"/"hpfax" before * before "usb" before "parallel" before
            # "serial"
            if other.type == "serial":
                return -1
            if self.type == "serial":
                return 1
            if other.type == "parallel":
                return -1
            if self.type == "parallel":
                return 1
            if other.type == "usb":
                return -1
            if self.type == "usb":
                return 1
            if other.type == "hp" or other.type == "hpfax":
                return 1
            if self.type == "hp" or self.type == "hpfax":
                return -1
        result = cmp(bool(self.id), bool(other.id))
        if not result:
            result = cmp(self.info, other.info)
        
        return result

class PrintersConf:
    def __init__(self, connection):
        self.device_uris = {}
        self.connection = connection
        self.parse(self.fetch('/admin/conf/printers.conf'))

    def fetch(self, file):
        fd, filename = tempfile.mkstemp("printer.conf")
        try:
            try:
                # Specifying the fd is allowed in pycups >= 1.9.38
                self.connection.getFile(file, fd=fd)
            except TypeError:
                self.connection.getFile(file, filename)
        except cups.HTTPError, e:
            os.close(fd)
            if (e.args[0] == cups.HTTP_UNAUTHORIZED or
                e.args[0] == cups.HTTP_NOT_FOUND):
                return []
            else:
                raise e

        os.close(fd)
        lines = open(filename).readlines()
        os.unlink(filename)
        return lines

    def parse(self, lines):
        current_printer = None
        for line in lines:
            words = line.split()
            if len(words) == 0:
                continue
            if words[0] == "DeviceURI":
                if len (words) >= 2:
                    self.device_uris[current_printer] = words[1]
                else:
                    self.device_uris[current_printer] = ''
            else:
                match = re.match(r"<(Default)?Printer ([^>]+)>\s*\n", line) 
                if match:
                    current_printer = match.group(2)
                if line.strip().find("</Printer>") != -1:
                    current_printer = None

def getDevices(connection):
    """
    raise cups.IPPError
    """
    devices = connection.getDevices()
    for uri, data in devices.iteritems():
        device = Device(uri, **data)
        devices[uri] = device
        if device.info != 'Unknown' and device.make_and_model == 'Unknown':
            device.make_and_model = device.info
    return devices

def activateNewPrinter(connection, name):
    """Set a new printer enabled, accepting jobs, and
    (if necessary) the default printer."""
    connection.enablePrinter (name)
    connection.acceptJobs (name)

    # Set as the default if there is not already a default printer.
    default_is_set = False
    try:
        if connection.getDefault () != None:
            default_is_set = True
    except AttributeError: # getDefault appeared in pycups-1.9.31
        dests = connection.getDests ()
        # If a default printer is set it will be available from
        # key (None,None).
        if dests.has_key ((None, None)):
            default_is_set = True

    if not default_is_set:
        connection.setDefault (name)

def getPPDGroupOptions(group):
    options = group.options[:]
    for g in group.subgroups:
        options.extend(getPPDGroupOptions(g))
    return options

def iteratePPDOptions(ppd):
    for group in ppd.optionGroups:
        for option in getPPDGroupOptions(group):
            yield option

def copyPPDOptions(ppd1, ppd2):
    for option in iteratePPDOptions(ppd1):
        if option.keyword == "PageRegion":
            continue
        new_option = ppd2.findOption(option.keyword)
        if new_option and option.ui==new_option.ui:
            value = option.defchoice
            for choice in new_option.choices:
                if choice["choice"]==value:
                    ppd2.markOption(new_option.keyword, value)
                    debugprint ("set %s = %s" % (new_option.keyword, value))
                    
def setPPDPageSize(ppd, language):
    # Just set the page size to A4 or Letter, that's all.
    # Use the same method CUPS uses.
    size = 'A4'
    letter = [ 'C', 'POSIX', 'en', 'en_US', 'en_CA', 'fr_CA' ]
    for each in letter:
        if language == each:
            size = 'Letter'
    try:
        ppd.markOption ('PageSize', size)
        debugprint ("set PageSize = %s" % size)
    except:
        debugprint ("Failed to set PageSize (%s not available?)" % size)

def missingPackagesAndExecutables(ppd):
    """Check that all relevant executables for a PPD are installed.

    ppd: cups.PPD object"""

    # First, a local function.  How to check that something exists
    # in a path:
    def pathcheck (name, path="/usr/bin:/bin"):
        # Strip out foomatic '%'-style place-holders.
        p = name.find ('%')
        if p != -1:
            name = name[:p]
        if len (name) == 0:
            return "true"
        if name[0] == '/':
            if os.access (name, os.X_OK):
                debugprint ("%s: found" % name)
                return name
            else:
                debugprint ("%s: NOT found" % name)
                return None
        if name.find ("=") != -1:
            return "builtin"
        if name in [ ":", ".", "[", "alias", "bind", "break", "cd",
                     "continue", "declare", "echo", "else", "eval",
                     "exec", "exit", "export", "fi", "if", "kill", "let",
                     "local", "popd", "printf", "pushd", "pwd", "read",
                     "readonly", "set", "shift", "shopt", "source",
                     "test", "then", "trap", "type", "ulimit", "umask",
                     "unalias", "unset", "wait" ]:
            return "builtin"
        for component in path.split (':'):
            file = component.rstrip (os.path.sep) + os.path.sep + name
            if os.access (file, os.X_OK):
                debugprint ("%s: found" % file)
                return file
        debugprint ("%s: NOT found in %s" % (name,path))
        return None

    pkgs_to_install = []
    exes_to_install = []

    # Find a 'FoomaticRIPCommandLine' attribute.
    exe = exepath = None
    attr = ppd.findAttr ('FoomaticRIPCommandLine')
    if attr:
        # Foomatic RIP command line to check.
        cmdline = attr.value.replace ('&&\n', '')
        cmdline = cmdline.replace ('&quot;', '"')
        cmdline = cmdline.replace ('&lt;', '<')
        cmdline = cmdline.replace ('&gt;', '>')
        if (cmdline.find ("(") != -1 or
            cmdline.find ("&") != -1):
            # Don't try to handle sub-shells or unreplaced HTML entities.
            cmdline = ""

        # Strip out foomatic '%'-style place-holders
        pipes = cmdline.split (';')
        for pipe in pipes:
            cmds = pipe.strip ().split ('|')
            for cmd in cmds:
                args = cmd.strip ().split (' ')
                exe = args[0]
                exepath = pathcheck (exe)
                if not exepath:
                    break

                # Main executable found.  But if it's 'gs',
                # perhaps there is an IJS server we also need
                # to check.
                if os.path.basename (exepath) == 'gs':
                    argn = len (args)
                    argi = 1
                    search = "-sIjsServer="
                    while argi < argn:
                        arg = args[argi]
                        if arg.startswith (search):
                            exe = arg[len (search):]
                            exepath = pathcheck (exe)
                            break

                        argi += 1

            if not exepath:
                # Next pipe.
                break

    if exepath or not exe:
        # Look for '*cupsFilter' lines in the PPD and check that
        # the filters are installed.
        (tmpfd, tmpfname) = tempfile.mkstemp ()
        ppd.writeFd (tmpfd)
        search = "*cupsFilter:"
        for line in file (tmpfname).readlines ():
            if line.startswith (search):
                line = line[len (search):].strip ().strip ('"')
                try:
                    (mimetype, cost, exe) = line.split (' ')
                except:
                    continue

                exepath = pathcheck (exe,
                                     "/usr/lib/cups/filter:"
                                     "/usr/lib64/cups/filter")

    if exe and not exepath:
        # We didn't find a necessary executable.  Complain.

        # Strip out foomatic '%'-style place-holders.
        p = exe.find ('%')
        if p != -1:
            exe = exe[:p]

        pkgs = {
            # Foomatic command line executables
            'gs': 'ghostscript',
            'perl': 'perl',
            'foo2oak-wrapper': None,
            'pnm2ppa': 'pnm2ppa',
            'c2050': 'c2050',
            'c2070': 'c2070',
            'cjet': 'cjet',
            'lm1100': 'lx',
            'esc-m': 'min12xxw',
            'min12xxw': 'min12xxw',
            'pbm2l2030': 'pbm2l2030',
            'pbm2l7k': 'pbm2l7k',
            'pbm2lex': 'pbm2l7k',
            # IJS servers (used by foomatic)
            'hpijs': 'hpijs',
            'ijsgutenprint.5.0': 'gutenprint',
            # CUPS filters
            'rastertogutenprint.5.0': 'gutenprint-cups',
            'commandtoepson': 'gutenprint-cups',
            'commandtocanon': 'gutenprint-cups',
            }
        try:
            pkg = pkgs[exe]
        except:
            pkg = None

        if pkg:
            debugprint ("%s included in package %s" % (exe, pkg))
            pkgs_to_install.append (pkg)
        else:
            exes_to_install.append (exe)

    return (pkgs_to_install, exes_to_install)

def main():
    c = cups.Connection()
    #printers = getPrinters(c)
    for device in getDevices(c).itervalues():
        print device.uri, device.id_dict

if __name__=="__main__":
    main()
