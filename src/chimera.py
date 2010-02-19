#!/usr/bin/env python

###############################################################################
#
# $Id$
#
###############################################################################

# system imports
import sys
import os
import errno
import stat
import pwd
import grp
import string
import time
import re
import types
import socket

# enstore imports
import Trace
import e_errors
#try:
#    import Devcodes # this is a compiled enstore module
#except ImportError:
#    Trace.log(e_errors.INFO, "Devcodes unavailable")
import option
import enstore_constants
import hostaddr
import enstore_functions2
import charset
import atomic
import file_utils

#ENABLED = "enabled"
#DISABLED = "disabled"
#VALID = "valid"
#INVALID =  "invalid"
UNKNOWN = "unknown"  #Same in namespace and pnfs.
#EXISTS = "file exists"
#DIREXISTS = "directory exists"
ERROR = -1

PATH_MAX = 199

##############################################################################

# FOr reverse lookup of dic
def find_key(dic, val):
   for item in dic.items():
       if item[1] == val:
          return item[0]
   return None
   #return [item[0] for item in dic.items() if item[1] == val]

#This is used to print out some of the results to the terminal that are more
# than one line long and contained in a list.  The list is usually generated
# by a f.readlines() where if is a file object.  Otherwise the result is
# printed as is.
def print_results(result):
    if type(result) == types.ListType:
         for line in result:
            print line, #constains a '\012' at the end.
    else:
        print result
def print_results2(result):
    if type(result) == types.ListType:
         for line in result:
            print line #no '\012' at the end.
    else:
        print result

#Make this shortcut so there is less to type.
fullpath = enstore_functions2.fullpath

#######################################################################

def layer_file(f, n):
    pn, fn = os.path.split(f)
    if is_access_name(fn):
        return os.path.join(pn, "%s(%d)" % (fn, n))
    else:
        return os.path.join(pn, ".(use)(%d)(%s)" % (n, fn))

def id_file(f):
    #We need to be careful that a .(access)() file does not get passed here. 
    pn, fn = os.path.split(f)
    return os.path.join(pn, ".(id)(%s)" % (fn, ))

def parent_file(f, pnfsid = None):
    pn, fn = os.path.split(f)
    if pnfsid:
        return os.path.join(pn, ".(parent)(%s)" % (pnfsid))
    if is_access_name(f):
        pnfsid = fn[10:-1]
        return os.path.join(pn, ".(parent)(%s)" % (pnfsid))
    else:
        fname = id_file(f)
        f = file_utils.open(fname)
        pnfsid = f.readline()
        f.close()
        return os.path.join(pn, ".(parent)(%s)" % (pnfsid))

def access_file(dn, pnfsid):
    return os.path.join(dn, ".(access)(%s)" % (pnfsid))

def database_file(directory):
    return os.path.join(directory, ".(get)(database)")


##############################################################################

def is_access_name(filepath):
    #Determine if it is an ".(access)()" name.
    access_match = re.compile("\.\(access\)\([0-9A-Fa-f]+\)")
    if re.search(access_match, os.path.basename(filepath)):
        return True

    return False

def is_layer_access_name(filepath):
    #Determine if it is an ".(access)(chimeraid)(1-8)" name.
    access_match = re.compile("\.\(access\)\([0-9A-Fa-f]+\)\([1-8]\)")
    if re.search(access_match, os.path.basename(filepath)):
        return True

    return False


def is_nameof_name(filepath):
    #Determine if it is an ".(access)()" name.
    nameof_match = re.compile("\.\(nameof\)\([0-9A-Fa-f]+\)")
    if re.search(nameof_match, os.path.basename(filepath)):
        return True

    return False


#This is common functionality used by 
# is_chimera_path,is_pnfs_path and  is_pnfs_or_chimera_path functions
def get_dirname_filename(pathname) :
    if is_access_name(pathname) or is_nameof_name(pathname):
        #We don't want to call fullpath() for these special files.
        # fullpath doesn't know how to protect from accessing an unknown
        # database (which causes the mount point to hang).
        basename = os.path.basename(pathname)
        dirname = get_directory_name(pathname)
        filename = os.path.join(dirname, basename)
    else:
        #Expand the filename to the absolute path.
        #unused, filename, dirname, unused = fullpath(pathname)
        filename = enstore_functions2.expand_path(pathname)


    #Some versions of python have gotten dirname wrong if filename was
    # already a directory.
    if os.path.isdir(filename):
        dirname = filename
        #basename = ""
    else:
        dirname = os.path.dirname(filename)
    return dirname, filename


def is_chimera_path(pathname, check_name_only = None):
    if not pathname:  #Handle None and empty string.
        return False
 
    dirname, filename = get_dirname_filename(pathname)
    
    #Determine if the target file or directory is in the pnfs namespace.
    if string.find(dirname,"/pnfs/") < 0:
        return False #If we get here it is not a pnfs directory.
    #print 'inside is_pnfs_or_chimera_path ',  pathname
    #Search all directories in the path for the cursor wormwhole file. These
    # extra steps are needed in case the user enters a name that does not
    # exist.
    search_dir = "/"
    for directory in dirname.split("/"):
        search_dir = os.path.join(search_dir, directory)
        
        #Determine the path for the cursor existance test.
        fname = os.path.join(search_dir, ".(get)(cursor)")

        #If the cursor 'file' does not exist, then this is not a real pnfs
        # file system.
        #print 'checking fname ', fname
        if os.path.exists(fname):
           break # return True
    else:
       return False #The ".(get)(cursor)" files was not found in any directory.

    # At this point we have determined that the pathname is either pnfs or chimera
    # Now we need to determine if it is only chimera or nor

    for directory in dirname.split("/"):
        search_dir = os.path.join(search_dir, directory)
        
        #Determine the path for the cursor existance test.
        fname = os.path.join(search_dir, ".(get)(database)")

        #If the database 'file' does not exist, then this is not a real chimera
        # file system.
        #print 'checking NEW fname' , fname
        if os.path.exists(fname):
           # It means that is itrs Pnfs
           return False
    
    # At this point we know that it is only Chimera and not Pnfs 

    #If the pathname existance test should be skipped, return true at
    # this time.
    if check_name_only:
        return True 
    
    #If check_name_only is python false then we can reach this check
    # that checks to make sure that the filename exists.  Use os.stat()
    # instead of os.path.exist(), since the later was found to be returning
    # ENOENT errors that really should have been EACCES errors.
    try:
        if file_utils.get_stat(filename):
            return True
    except OSError, msg:
        if msg.args[0] == errno.ENOENT:
            pass
        else:
            return True
    
    #If we get here, then the path contains a directory named 'pnfs' but does
    # not point to a pnfs directory.
    return False

def is_normal_pnfs_path(pathname, check_name_only = None):
    rtn = is_chimera_path(pathname, check_name_only)
    if rtn:
        #Additional check to make sure that this is a normal path.  Remove
        # the directory component seperator "/" from the character list.
        basename_charset = charset.filenamecharset.replace("/", "")
        if re.search("/pnfs/[%s]*/usr/" % (basename_charset), pathname):
            rtn = 0 #Admin path.

    return rtn


def is_admin_pnfs_path(pathname, check_name_only = None):
    #print "INSIDE is_admin_pnfs_path"
    rtn = is_chimera_path(pathname, check_name_only)
    # No admin path for chimera
    return rtn
    """
    if rtn:
        #Additional check to make sure that this is an admin path.  Remove
        # the directory component seperator "/" from the character list.
        basename_charset = charset.filenamecharset.replace("/", "")
        if not re.search("/pnfs/[%s]*/usr/" % (basename_charset), pathname):
            rtn = 0 #Normal path.

    return rtn
    """
def isdir(pathname):
    return os.path.isdir(pathname)

def isfile(pathname):
    return os.path.isfile(pathname)

def islink(pathname):
    return os.path.islink(pathname)

def is_chimeraid(pnfsid):
    #This is an attempt to deterime if a string is a pnfsid.
    # 1) Is it a string?
    # 2) Is it 24 characters long?
    # 3) All characters are in the capital hex character set.
    #Note: Does it need to be capital set of hex characters???
    len_of_pnfsid = len(pnfsid)
    if type(pnfsid) == types.StringType and len_of_pnfsid == 36:
        allowable_characters = string.upper(string.hexdigits)
        for c in pnfsid:
            if c not in allowable_characters:
                return 0
        else: #success
            return 1
    return 0

##############################################################################

#Remove the /pnfs/, /pnfs/fnal.gov/usr or /pnfs/fs/usr/ from the pnfs path.
def strip_pnfs_mountpoint(pathname):
    tmp1 = pathname[pathname.find("/pnfs/"):]
    tmp2 = tmp1[6:]
    
    #Determine the canonical path base.  (i.e /pnfs/fnal.gov/usr/)
    # If the ENCP_CANONICAL_DOMAINNAME overriding environmental variable
    # is set, use that.
    if os.environ.get('ENCP_CANONICAL_DOMAINNAME', None):
        canonical_name = os.environ['ENCP_CANONICAL_DOMAINNAME']
    else:
        canonical_name = string.join(socket.getfqdn().split(".")[1:], ".")
    canonical_path = os.path.join(canonical_name, "usr")

    if tmp2[:7] == "fs/usr/":
        tmp3 = tmp2[7:]
    elif tmp2[:len(canonical_path)] == canonical_path:
        tmp3 = tmp2[len(canonical_path):]
    else:
        tmp3 = tmp2
    return tmp3

def get_directory_name(filepath):

    if type(filepath) != types.StringType:
        return None

    #If we already have a directory...
    #if os.path.isdir(filepath):
    #    return filepath

    #Determine if it is an ".(access)()" name.
    if is_access_name(filepath):
        #Since, we have the .(access)() name we need to split off the id.
        dirname, filename = os.path.split(filepath)
        pnfsid = filename[10:-1]  #len(".(access)(") == 10 and len ")" == 1
        #We will need the pnfs database numbers.
        """
        use_pnfsid_db=int(pnfsid[:4], 16)

        #If the mountpoint doesn't know about our database fail now.
        try:
            N(use_pnfsid_db, dirname).get_databaseN(use_pnfsid_db)
        except (OSError, IOError), msg:
            if msg.args[0] == errno.ENOENT:
                raise OSError(errno.ENOENT,
                              "No such database: (%s, %s)" % (use_pnfsid_db,
                                                              dirname))
            else:
                raise OSError(msg.args[0],
                              "Error accessing database: (%s, %s): %s" % \
                              (use_pnfsid_db, dirname, str(msg)))
        """
        #Create the filename to obtain the parent id.
        parent_id_name = os.path.join(dirname, ".(parent)(%s)" % pnfsid)
        #Read the parent id.
        f = file_utils.open(parent_id_name)
        parent_id = f.readlines()[0].strip()
        f.close()

        #Build the .(access)() filename of the parent directory.
        directory_name = os.path.join(dirname, ".(access)(%s)" % parent_id)

    else:
        directory_name = os.path.dirname(filepath)
   
    return directory_name

###############################################################################

def get_database(f):
    return "admin:0:r:enabled:/srv2/pnfs/db/admin"

def get_layer(layer_filename, max_lines = None):
    RETRY_COUNT = 2
    
    i = 0
    while i < RETRY_COUNT:
        # get info from layer
        try:
            fl = file_utils.open(layer_filename)
            if max_lines:
                layer_info = []
                i = 0
                while i < max_lines:
                    layer_info.append(fl.readline())
                    i = i + 1
            else:
                layer_info = fl.readlines()
            fl.close()
            break
        except (OSError, IOError), detail:
            #Increment the retry count before it is needed to determine if
            # we should sleep or not sleep.
            i = i + 1
            
            if detail.args[0] in [errno.EACCES, errno.EPERM] and os.getuid() == 0:
                #If we get here and the real id is user root, we need to reset
                # the effective user id back to that of root ...
                try:
                    os.seteuid(0)
                    os.setegid(0)
                except OSError:
                    pass
            elif i < RETRY_COUNT:
                #If the problem wasn't permissions, lets give the system a
                # moment to catch up.
                #Skip the sleep if we are not going to try again.
                ##time.sleep(0.1)

                ##It is known that stat() can return an incorrect ENOENT
                ## if pnfs is really loaded.  Is this true for open() or
                ## readline()?  Skipping the time.sleep() makes the scan
                ## much faster.
                raise detail
    else:
        raise detail

    return layer_info

def get_layer_1(f):
    # get bfid from layer 1
    try:
        bfid = get_layer(layer_file(f, 1))
    except (OSError, IOError), detail:
        bfid = None
        if detail.errno in [errno.EACCES, errno.EPERM]:
            raise OSError(detail.errno, "no read permissions for layer 1",
                          detail.filename)
        elif detail.args[0] in [errno.ENOENT, errno.EISDIR]:
            pass
        else:
            raise OSError(errno.EIO, "corrupted layer 1 metadata",
                          detail.filename)

    try:
        bfid = bfid[0].strip()
    except:
        bfid = ""

    return bfid


def get_layer_2(f):
    # get dcache info from layer 2
    try:
        layer2 = get_layer(layer_file(f, 2))
    except (OSError, IOError), detail:
        layer2 = None
        if detail.errno in [errno.EACCES, errno.EPERM]:
            raise OSError(detail.errno, "no read permissions for layer 2",
                          detail.filename)
        elif detail.args[0] in [errno.ENOENT, errno.EISDIR]:
            pass
        else:
            raise OSError(errno.EIO, "corrupted layer 2 metadata",
                          detail.filename)

    l2 = {}
    if layer2:
        try:
            l2['line1'] = layer2[0].strip()
        except IndexError:
            l2['line1'] = None

        try:
            line2 = layer2[1].strip()
        except IndexError:
            line2 = ""

        try:
            hsm_match = re.compile("h=(no|yes)")
            l2['hsm'] = hsm_match.search(line2).group().split("=")[1]
        except AttributeError:
            l2['hsm'] = None

        try:
            crc_match = re.compile("c=[1-9]+:[a-zA-Z0-9]{8}")
            l2['crc'] = long(crc_match.search(line2).group().split(":")[1], 16)
        except AttributeError:
            l2['crc'] = None

        try:
            size_match = re.compile("l=[0-9]+")
            l2['size'] = long(size_match.search(line2).group().split("=")[1])
        except AttributeError:
            l2['size'] = None

        l2['pools'] = []
        for item in layer2[2:]:
            l2['pools'].append(item.strip())

    return l2

def get_layer_4(f, max_lines = None):
    # get xref from layer 4 (?)
    try:
        layer4 = get_layer(layer_file(f, 4), max_lines)
    except (OSError, IOError), detail:
        layer4 = None
        if detail.errno in [errno.EACCES, errno.EPERM]:
            raise OSError(detail.errno, "no read permissions for layer 4",
                          detail.filename)
        elif detail.args[0] in [errno.ENOENT, errno.EISDIR]:
            pass
        else:
            raise OSError(errno.EIO, "corrupted layer 4 metadata",
                          detail.filename)

    l4 = {}
    if layer4:
        try:
            l4['volume'] = layer4[0].strip()
        except IndexError:
            pass
        try:
            l4['location_cookie'] = layer4[1].strip()
        except IndexError:
            pass
        try:
            l4['size'] = layer4[2].strip()
        except IndexError:
            pass
        try:
            l4['file_family'] = layer4[3].strip()
        except IndexError:
            pass
        try:
            l4['original_name'] = layer4[4].strip()
        except IndexError:
            pass
        # map file no longer used
        try:
            l4['pnfsid'] = layer4[6].strip()
        except IndexError:
            pass
        # map pnfsid no longer used
        try:
            l4['bfid'] = layer4[8].strip()
        except IndexError:
            pass
        try:
            l4['drive'] = layer4[9].strip() #optionally present
        except IndexError:
            pass
        try:
            l4['crc'] = layer4[10].strip() #optionally present
        except IndexError:
            pass

    return l4

def get_pnfsid(f):
    if is_access_name(f):
        pnfsid = os.path.basename(f)[10:-1]
        return pnfsid
    
    #Get the id of the file or directory.
    try:
        fname = id_file(f)
        f = file_utils.open(fname)
        pnfs_id = f.readline().strip()
        f.close()
    except(OSError, IOError), detail:
        pnfs_id = None
        if not detail.errno == errno.ENOENT or not os.path.ismount(f):
            message = "%s: %s" % (os.strerror(detail.errno),
                                  "unable to obtain pnfs id")
            raise OSError(detail.errno, message, fname)

    return pnfs_id

###############################################################################

#Global cache.
db_pnfsid_cache = {}
last_db_tried = ("", (-1, ""))

#Get currently mounted pnfs mountpoints.
def parse_mtab():
    #print 'INSIDE parse_mtab'
    #Different systems have different names for this file.
    # /etc/mtab: Linux, IRIX
    # /etc/mnttab: SunOS
    # MacOS doesn't have one.
    for mtab_file in ["/etc/mtab", "/etc/mnttab"]:
        try:
            fp = file_utils.open(mtab_file, "r")
            mtab_data = fp.readlines()
            fp.close()
            break
        except OSError, msg:
            if msg.args[0] in [errno.ENOENT]:
                continue
            else:
                raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
    else:
        #Should this raise an error?
        mtab_data = []

    found_mountpoints = {}
    index = 0
    for line in mtab_data:
        #The 2nd and 3rd items in the list are important to us here.
        data = line[:-1].split()
        mp = data[1]
        fs_type = data[2]

        #If the filesystem is not an NFS filesystem, skip it.
        if fs_type != "nfs":
            continue

        # To figure out if the nfs mount is really a chimera/pnfs 
        # we run a tags command. If exception is raised, then it is not 
        # pnfs/chimera mount
        try:
            dataname = os.path.join(mp, ".(tags)()")
            db_fp = file_utils.open(dataname, "r")
            db_fp.readline().strip()
            db_fp.close()
        except IOError:
            continue
        #To generate a serial index  used as akey in the return dictionary
        db_data = "admin:0:r:enabled:/" + str(index)
        index += 1
        db_datas = db_data.split(":")
        #db_datas[0] is the database name
        #db_datas[1] is the database id
        #db_datas[2] is the database (???)
        #db_datas[3] is the database enabled or disabled status
        #db_datas[4] is the database (???)

        #If the database's id is not in the cache, add it along with the
        # mount point that goes with it.
        db_pnfsid = int(db_datas[1])
        #if db_data not in db_pnfsid_cache.keys():
        #    db_pnfsid_cache[db_data] = (db_pnfsid, mp)
        if db_data not in found_mountpoints.keys():
            found_mountpoints[db_data] = (db_pnfsid, mp)

    return found_mountpoints


def set_last_db(database_values):
    global last_db_tried

    ## database_info: Should be the pnfs --database output.  Something like:
    ##   cms:9:r:enabled:/diskb/pnfs/db/cms
    ##
    ## database_number: The number of the database.  This should match the
    ## second part of the database_info line.
    ##
    ## mount_point: The current location of the mount point for this
    ## pnfs database.
    database_info = database_values[0]
    database_number = database_values[1][0]
    mount_point = database_values[1][1]
    
    last_db_tried = (database_info, (database_number, mount_point))

def get_last_db():
    global last_db_tried
    return last_db_tried

def process_mtab():
    global db_pnfsid_cache
    
    if not db_pnfsid_cache:
        #Sets global db_pnfsid_cache.
        db_pnfsid_cache = parse_mtab()

    return [last_db_tried] + sort_mtab()

def __db_cmp(x, y):
    is_x_fs_usr = x[1][1].find("/fs/usr/") > 0
    is_y_fs_usr = y[1][1].find("/fs/usr/") > 0

    is_x_fs = x[1][0] == 0
    is_y_fs = y[1][0] == 0

    #Always put /pnfs/fs last.
    if is_x_fs and not is_y_fs:
        return 1
    elif not is_x_fs and is_y_fs:
        return -1

    #Always put /pnfs/xyz first.
    elif is_x_fs_usr and not is_y_fs_usr:
        return 1
    elif not is_x_fs_usr and is_y_fs_usr:
        return -1

    #The are the same type of path.  Sort by db number.
    if x[1][0] < y[1][0]:
        return 1
    elif x[1][0] > y[1][0]:
        return -1

    return 0

def sort_mtab():
    global db_pnfsid_cache

    search_list = db_pnfsid_cache.items()
    #By sorting and reversing, we can leave db number 0 (/pnfs/fs) in
    # the list and it will be sorted to the end of the list.
    search_list.sort(lambda x, y: __db_cmp(x, y))

    #import pprint
    #pprint.pprint(search_list)
    #sys.exit(1)

    return search_list

def add_mtab(db_info, db_num, db_mp):
    global db_pnfsid_cache

    if db_info not in db_pnfsid_cache.keys():
        db_pnfsid_cache[db_info] = (db_num, db_mp)
        sort_mtab()

###############################################################################

#Return a list of admin (/pnfs/fs like) mount points.
def get_enstore_admin_mount_point(pnfsid = None):

    list_of_admin_mountpoints = []

    #Get the list of pnfs mountpoints currently mounted.
    mtab_results = parse_mtab()
    
    for db_num, mount_path in mtab_results.values():
        if db_num == 0:  #Admin db has number 0.
            if os.path.basename(mount_path) == "fs":
                mount_path = os.path.join(mount_path, "usr")
            
            if pnfsid == None:
                list_of_admin_mountpoints.append(mount_path)
            else:
                access_path = access_file(mount_path, pnfsid)
                try:
                    file_utils.get_stat(access_path)
                except OSError, msg:
                    if msg.errno in [errno.ENOENT]:
                        continue
                    else:
                        list_of_admin_mountpoints.append(mount_path)

                        
    return list_of_admin_mountpoints

#Return a list of admin (/pnfs/fs like) mount points.
def get_enstore_mount_point(pnfsid = None):

    list_of_admin_mountpoints = []

    #Get the list of pnfs mountpoints currently mounted.
    mtab_results = parse_mtab()
    
    for db_num, mount_path in mtab_results.values():
        if db_num != 0:  #Admin db has number 0.
            #if os.path.basename(mount_path) == "fs":
            #    mount_path = os.path.join(mount_path, "usr")
            
            if pnfsid == None:
                list_of_admin_mountpoints.append(mount_path)
            else:
                access_path = access_file(mount_path, pnfsid)
                try:
                    file_utils.stat(access_path)
                except OSError, msg:
                    if msg.errno in [errno.ENOENT]:
                        continue
                    else:
                        list_of_admin_mountpoints.append(mount_path)

                        
    return list_of_admin_mountpoints

###############################################################################

#filepath should refer to a pnfs path.
#replacement_path should be one of "/pnfs/", "/pnfs/fnal.gov" or "/pnfs/".
def __get_special_path(filepath, replacement_path):
    #Make sure this is a string.
    if type(filepath) != types.StringType:
        raise TypeError("Expected string filename.",
                        e_errors.WRONGPARAMETER)
    #Make sure this is a string.
    if type(replacement_path) != types.StringType:
        raise TypeError("Expected string replacement string.",
                        e_errors.WRONGPARAMETER)

    #Make absolute path.
    #Note: enstore_functions2.fullpath() does a stat() to determine if filepath
    # is a directory (it appends a / to filename if so).  We know we don't
    # need it here.  Just use expand_path here for performance gains.
    #unused, filename, dirname, unused = enstore_functions2.fullpath(filepath)
    filename = enstore_functions2.expand_path(filepath)

    #Determine the canonical path base.  (i.e /pnfs/fnal.gov/usr/)
    # If the ENCP_CANONICAL_DOMAINNAME overriding environmental variable
    # is set, use that.
    if os.environ.get('ENCP_CANONICAL_DOMAINNAME', None):
        canonical_name = os.environ['ENCP_CANONICAL_DOMAINNAME']
    else:
        canonical_name = string.join(socket.getfqdn().split(".")[1:], ".")
    canonical_name = string.join(socket.getfqdn().split(".")[1:], ".")
    canonical_pathbase = os.path.join("/pnfs", canonical_name, "usr") + "/"

    #Return an error if the file is not a pnfs filename.
    #if not pnfs.is_chimera_path(dirname, check_name_only = 1):
    #    raise EncpError(None, "Not a pnfs filename.", e_errors.WRONGPARAMETER)

    #Build the list of patters to search for.  Start with the three we
    # know about...
    pattern_list = ["/pnfs/fs/usr/", canonical_pathbase, "/pnfs/"]
    
    ##However, we need to handle paths like matching /pnfs/fs/usr/dzero
    ## with /pnfs/sam/dzero (instead of the more obvious /pnfs/dzero).
    
    #First, remove any preceding directories before /pnfs/.
    dir_split = filepath.split("/")
    try:
        dir_split_index = dir_split.index("pnfs")
    except ValueError:
        #The file is not a pnfs file.
        raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), filename)
        
    #Limit this check to just three directory levels after /pnfs/.  If it 
    # hasn't been found by then, chances are it will not.  If necessary,
    # this could be increased.
    dir_split = dir_split[dir_split_index : dir_split_index + 3]

    #Next, start putting those directories into the pattern match list.
    current_dir_name = "/"
    for dir_name in dir_split:
        current_dir_name = os.path.join(current_dir_name, dir_name) + "/"
        pattern_list.append(current_dir_name)

    ## Check to make sure that the current pattern exists.  If so, return
    ## it.
    for pattern in pattern_list:
        filename, count = re.subn(pattern, replacement_path, filepath, 1)
        if count > 0 and is_chimera_path(filename, check_name_only = 1):
            return filename

    #The file is not a pnfs file.
    raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), filepath)

def get_enstore_pnfs_path(filepath):
    return __get_special_path(filepath, "/pnfs/")


def get_enstore_fs_path(filepath):
    return __get_special_path(filepath, "/pnfs/fs/usr/")


def get_enstore_canonical_path(filepath):
    #Determine the canonical path base.  (i.e /pnfs/fnal.gov/usr/)
    # If the ENCP_CANONICAL_DOMAINNAME overriding environmental variable
    # is set, use that.
    if os.environ.get('ENCP_CANONICAL_DOMAINNAME', None):
        canonical_name = os.environ['ENCP_CANONICAL_DOMAINNAME']
    else:
        canonical_name = string.join(socket.getfqdn().split(".")[1:], ".")
    #Use the canonical_name to determine the canonical pathname base.
    canonical_pathbase = os.path.join("/pnfs", canonical_name, "usr") + "/"
    
    return __get_special_path(filepath, canonical_pathbase)

###############################################################################

class Pnfs:# pnfs_common.PnfsCommon, pnfs_admin.PnfsAdmin):
    # initialize - we will be needing all these things soon, get them now
    #
    #pnfsFilename: The filename of a file in pnfs.  This may also be the
    #              pnfs id of a file in pnfs.
    #mount_point: The mount point that the file should be under when
    #             pnfsFilename is really a pnfsid or pnfsFilename does
    #             not contain an absolute path.
    #shortcut: If passed a pnfsid and this is true, don't lookup the
    #          full filepath.  Use the .../.(access)(%s) name instead.
    def __init__(self, pnfsFilename="", mount_point="", shortcut=None):

                 #get_details=1, get_pinfo=0, timeit=0, mount_point=""):

        #self.print_id = "PNFS"
        self.mount_point = mount_point
        #Make sure self.id exists.  __init__ should set it correctly
        # if necessary a little later on.
        self.id = None

        if mount_point:
            self.dir = mount_point
        else:
            try:
                #Handle the case where the cwd has been deleted.
                self.dir = os.getcwd()
            except OSError:
                self.dir = ""

        #Test if the filename passed in is really a pnfs id.
        if is_chimeraid(pnfsFilename):
            self.id = pnfsFilename
            try:
                if shortcut:
                    raise ValueError, "Applying filename shortcut"

                pnfsFilename_list = self.get_path(self.id)
                if len(pnfsFilename_list) == 1:
                    pnfsFilename = pnfsFilename_list[0]
                else:
                    sys.stderr.write("Found %d file matches instead of just 1.\n"
                                     % (len(pnfsFilename_list),))
                    sys.exit(1)
            except (OSError, IOError, AttributeError, ValueError):
                #No longer do just the following: pnfsFilename = ""
                # on an exception.  Attempt to get the ".(access)(<pnfs id>)"
                # version of the filename.
                #This was done in response to the pnfs database being
                # corrupted.  There was a directory that had fewer entries
                # than valid i-nodes that belonged in that directory.  With
                # this type of database corruption, the is_chimera_path() test
                # still works correctly.
                try:
                    dir_list, target = self._get_mount_point2(self.id,
                                                              self.dir,
                                                              ".(nameof)(%s)")
                    self.dir = dir_list[0]
                    pnfsFilename = os.path.join(self.dir,
                                                ".(access)(%s)" % self.id)
                except OSError, msg:
                    #If we got the ENODEV errno, it means that the same
                    # pnfsid was found under two different pnfs mount points.
                    # For this error we keep going, but for all others
                    # re-raise the traceback.
                    if msg.args[0] not in [errno.ENODEV]:
                        raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]

                if not is_chimera_path(pnfsFilename):
                    pnfsFilename = ""

        if pnfsFilename:
            (self.machine, self.filepath, self.dir, self.filename) = \
                           fullpath(pnfsFilename)

            if shortcut and self.id:
                #We need to determine the .(accesses)() path name to
                # the directory of the file.
                parent_id = self.get_parent(id = self.id, directory = self.dir)
                use_dir = os.path.join(self.dir,
                                       ".(access)(%s)" % parent_id)

                #This block of code determines if the use_dir path is a
                # directory or not.  The parent of a tag file is another
                # tag file.  So, we leave self.dir alone for these cases and
                # set it only when we really do have a directory.
                try:
                    f_stats = file_utils.get_stat(use_dir)
                    if stat.S_ISDIR(f_stats[stat.ST_MODE]):
                        #We have the pnfs id of a tag file.
                        self.dir = use_dir
                except (OSError, IOError), msg:
                    if msg.args[0] != errno.ENOTDIR:
                        #We have the pnfs id of a tag file.
                        self.dir = use_dir
                
            self.pstatinfo()

        try:
            self.pnfsFilename = self.filepath
        except AttributeError:
            #sys.stderr.write("self.filepath DNE after initialization\n")
            pass

    ##########################################################################

    def layer_file(self, f, n):
        pn, fn = os.path.split(f)
        if is_access_name(fn):
            return os.path.join(pn, "%s(%d)" % (fn, n))
        else:
            return os.path.join(pn, ".(use)(%d)(%s)" % (n, fn))

    def id_file(self, f):
        pn, fn = os.path.split(f)
        if is_access_name(fn):
            #Just a note:  This is silly.  Finding out the pnfs id when the
            # id is already in the .(access)(<pnfsid>) name.  However,
            # we should be able to handle this, just in case.  The nameof
            # lookup is limited to just the parent directory and not the entire
            # path.
            
            #Since, we have the .(access)() name we need to split off the id.
            pnfsid = fn[10:-1]  #len(".(access)(") == 10 and len ")" == 1
            parent_id = self.get_parent(pnfsid, pn) #Get parent id
            nameof = self.get_nameof(pnfsid, pn) #Get nameof file

            #Create the filename to obtain the parent id.
            return os.path.join(pn, ".(access)(%s)" % parent_id,
                                ".(id)(%s)" % nameof)
        else:
            return os.path.join(pn, ".(id)(%s)" % (fn, ))

    def parent_file(self, f, pnfsid = None):
        pn, fn = os.path.split(f)
        if pnfsid:
            if os.path.isdir(f):
                return os.path.join(f, ".(parent)(%s)" % (pnfsid))
            else:
                return os.path.join(pn, ".(parent)(%s)" % (pnfsid))
        else:
            fname = self.id_file(f)
            f = file_utils.open(fname)
            pnfsid = f.readline()
            f.close()
            return os.path.join(pn, ".(parent)(%s)" % (pnfsid))

    def access_file(self, pn, pnfsid):
        return os.path.join(pn, ".(access)(%s)" % pnfsid)
            
    def use_file(self, f, layer):
        pn, fn = os.path.split(f)
        if is_access_name(fn):
            #Use the .(access)() extension path for layers.
            return "%s(%s)" % (f, layer)
        else:
            return os.path.join(pn, '.(use)(%d)(%s)' % (layer, fn))

    def fset_file(self, f, size):
        pn, fn = os.path.split(f)
        if is_access_name(fn):
            pnfsid = fn[10:-1]  
            name = self.get_nameof(pnfsid, pn)
            directory = pn
        else:
            directory = pn
            name = fn
           
        return os.path.join(directory, ".(fset)(%s)(size)(%s)" % (name, size))

    def nameof_file(self, pn, pnfsid):
        return os.path.join(pn, ".(nameof)(%s)" % (pnfsid,))

    def const_file(self, f):
        pn, fn = os.path.split(f)
        if is_access_name(fn):
            pnfsid = fn[10:-1]  #len(".(access)(") == 10 and len ")" == 1
            parent_id = self.get_parent(pnfsid, pn)

            directory = os.path.join(pn, ".(access)(%s)" % parent_id)
            name = self.get_nameof(pnfsid, pn)
        else:
            directory = pn
            name = fn
            
        return os.path.join(directory, ".(const)(%s)" % (name,))

    ##########################################################################

    #Convert a nameof, parent or showid filename to an access filename.
    def convert_to_access(self, pfn):
        dirname, fname = os.path.split(pfn)
        fname = fname.replace(".(nameof)", ".(access)", 1)
        fname = fname.replace(".(parent)", ".(access)", 1)
        fname = fname.replace(".(showid)", ".(access)", 1)
        return os.path.join(dirname, fname)

    ##########################################################################

    # list what is in the current object
    def dump(self):
        #Trace.trace(14, repr(self.__dict__))
        print repr(self.__dict__)


    #This function is used to test for various conditions on the file.
    # The purpose of this function is to hide the hidden files associated
    # with each real file.
    def verify_existance(self, filepath=None):
        if filepath:
            fname = filepath
        else:
            fname = self.filepath

        #Perform only one stat() and do the checks here for performance
        # improvements over calling python library calls for each check.
        # get_stat() is not used here because that function may return
        # the status of the parent directory instead, which is not what we
        # want here.
        pstat = file_utils.get_stat(fname)
        if not filepath:
            self.pstat = pstat

        #As long as the file exists root can read it.  What about writes?
        if os.geteuid() == 0:
            return
        
        #Using the stat, make sure that the "file" is readable.
        elif pstat[stat.ST_MODE] & stat.S_IROTH:
            return
        
        elif pstat[stat.ST_MODE] & stat.S_IRUSR and \
           pstat[stat.ST_UID] == os.geteuid():
            return

        elif pstat[stat.ST_MODE] & stat.S_IRGRP and \
           pstat[stat.ST_GID] == os.getegid():
            return
        
        else:
            raise OSError(errno.EACCES,
                          os.strerror(errno.EACCES) + ": " + fname)

        #if not os.path.exists(fname):
        #    raise OSError(errno.ENOENT,
        #                  os.strerror(errno.ENOENT) + ": " + fname)
        #
        #if not os.access(fname, os.R_OK):
        #    raise OSError(errno.EACCES,
        #                  os.strerror(errno.EACCES) + ": " + fname)

    ##########################################################################

    # create a new file or update its times
    def touch(self, filename=None):
        if not filename:
            use_filename = self.pnfsFilename
        else:
            use_filename = filename
            
        try:
            self.utime(use_filename)
        except os.error, msg:
            if msg.errno == errno.ENOENT:
                f = file_utils.open(use_filename,'w')
                f.close()
            else:
                Trace.log(e_errors.INFO,
                          "problem with pnfsFilename = " + use_filename)
                raise os.error, msg

        if not filename:
            self.pstatinfo()

    # create a new file
    def creat(self, filename=None, mode = None):
        if filename:
            fname = filename
        else:
            fname = self.pnfsFilename

        if mode:
            fd = atomic.open(fname, os.O_RDWR | os.O_CREAT | os.O_EXCL,
                             mode=mode)
        else:
            fd = atomic.open(fname, os.O_RDWR | os.O_CREAT | os.O_EXCL)

	if not filename:
            self.pstatinfo()

        os.close(fd)

    # update the access and mod time of a file
    def utime(self, filename=None):
        if not filename:
            filename = self.pnfsFilename

        t = int(time.time())
        file_utils.utime(filename,(t,t))
        
    # delete a pnfs file including its metadata
    def rm(self, filename=None):
        if not filename:
            filename = self.pnfsFilename
            
        self.writelayer(1,"", filename)
        self.writelayer(2,"", filename)
        self.writelayer(3,"", filename)
        self.writelayer(4,"", filename)

        # It would be better to move the file to some trash space.
        # I don't know how right now.
        file_utils.remove(filename)

        self.pstatinfo()

    ##########################################################################

    # write a new value to the specified file layer (1-7)
    # the file needs to exist before you call this
    def writelayer(self, layer, value, filepath=None):
        #print "Wrinting layeeeeerrrrrr ", layer,value 
        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.filepath

        fname = self.use_file(use_filepath, layer)

        #If the value isn't a string, make it one.
        if type(value)!=types.StringType:
            value=str(value)

        f = file_utils.open(fname,'w')
        f.write(value)
        f.close()
        #self.utime()
        #self.pstatinfo()

    # read the value stored in the requested file layer
    def readlayer(self, layer, filepath=None):
        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.filepath

        fname = self.use_file(use_filepath, layer)
            
        f = file_utils.open(fname,'r')
        l = f.readlines()
        f.close()
        
        return l

    ##########################################################################

    # get the const info of the file, given the filename
    def get_const(self, filepath=None):

        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.filepath

        fname = self.const_file(use_filepath)

        f=file_utils.open(fname,'r')
        const = f.readlines()
        f.close()

        if not filepath:
            self.const = const
        return const

    # get the numeric pnfs id, given the filename
    def get_id(self, filepath=None):

        if filepath:
            (directory, name) = os.path.split(filepath)
        else:
            (directory, name) = os.path.split(self.filepath)

        if is_access_name(name):
            pnfs_id = name[10:-1]  #len(".(access)(") == 10 and len ")" == 1
        else:
            fname = os.path.join(directory, ".(id)(%s)" % (name,))

            f = file_utils.open(fname, 'r')
            pnfs_id = f.readlines()
            f.close()

            pnfs_id = string.replace(pnfs_id[0], '\n', '')
            
        if not filepath:
            self.id = pnfs_id
        return pnfs_id

    ##########################################################################

    def get_showid(self, id=None, directory=""):

        if directory:
            use_dir = directory
        else:
            use_dir = self.dir
        
        if id:
            use_id = id
        else:
            use_id = self.id

        search_path, showid = self._get_mount_point2(use_id, use_dir,
                                                     ".(showid)(%s)")
        showid = showid[0]
        if not id:
            self.showid = showid
        return id

    #A smaller faster version of get_nameof().
    def _get_nameof(self, id, directory):
        fname = self.nameof_file(directory, id)

        f = file_utils.open(fname,'r')
        nameof = f.readline()
        f.close()

        return nameof.replace("\n", "")
        
    # get the nameof information, given the id
    def get_nameof(self, id=None, directory=""):

        if directory:
            use_dir = directory
        else:
            use_dir = self.dir

        if id:
            use_id = id
        else:
            use_id = self.id

        search_path, target = self._get_mount_point2(use_id, use_dir,
                                                     ".(nameof)(%s)")
        
        nameof = target[0].replace("\n", "")

        if not id:
            self.nameof = nameof
        return nameof

    #A smaller faster version of get_parent().
    def _get_parent(self, id, directory):
        fname = self.parent_file(directory, id)

        f = file_utils.open(fname,'r')
        parent = f.readline()
        f.close()

        return parent.replace("\n", "")

    # get the parent information, given the id
    def get_parent(self, id=None, directory=""):
        if directory:
            use_dir = directory
        else:
            use_dir = self.dir
        
        if id:
            use_id = id
        else:
            use_id = self.id

        search_path, target = self._get_mount_point2(use_id, use_dir,
                                                     ".(parent)(%s)")
        
        parent = target[0].replace("\n", "")

        if not id:
            self.parent = parent
        return parent

    # get the total path of the id
    def get_path(self, id=None, directory="", shortcut=None):
        if directory:
            #print "directory:", directory
            use_dir = fullpath(directory)[1]
        else:
            #print "self.dir:", self.dir
            use_dir = self.dir

        if id != None:
            if is_chimeraid(id):
                use_id = id
            else:
                raise ValueError("The pnfs id (%s) is not valid." % id)
        elif self.id != None:
            if is_chimeraid(self.id):
                use_id = self.id
            else:
                raise ValueError("The pnfs id (%s) is not valid." % self.id)
        else:
            raise ValueError("No valid pnfs id.")

        #print "use_id:", use_id
        #print "usd_dir:", use_dir
        try:
            search_path, target = self._get_mount_point2(use_id, use_dir,
                                                         ".(nameof)(%s)",
                                                         return_all = True)
            search_paths = search_path
            targets = target
        except OSError, msg:
            if msg.args[0] in [errno.ENODEV]:
                if msg.filename:
                    search_paths = msg.filename
                elif len(msg.args) >= 3 and msg.args[2]:
                    search_paths = msg.args[2]
                else:
                    search_paths = []
                targets = msg.args[3]
            else:
                #print "9999999999999999999999999999999999999999999"
                raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]

        rtn_filepaths = []
        for i in range(len(search_paths)):
            #print "xxxxxxxxxxxxxxxxxxxxxxx", search_paths[i], targets[i]
            rtn_filepaths.append(self.__get_path(use_id, search_paths[i],
                                                 targets[i], shortcut))
            #print "yyyyyyyyyyyyyyyyyyyyy", rtn_filepaths[-1]

        if len(rtn_filepaths) == 1:
            return rtn_filepaths
        else:
            raise OSError(errno.ENODEV,
                          "%s: %s" % (os.strerror(errno.ENODEV),
                                      "Too many matching mount points",),
                          rtn_filepaths)
    def __get_path(self, use_id, search_path, target, shortcut):
        filepath = target.replace("\n", "")

        #At this point 'filepath' contains just the basename of the file
        # with the "use_id" pnfs id.

        #If the user doesn't want the pain of going through a full name
        # lookup, return this alternate name.
        #the shortcut doesnot work with chimera
        if shortcut:
            pass
        #    return os.path.join(search_path, ".(access)(%s)" % use_id)

        #Loop through the pnfs ids to find each ids parent until the "root"
        # id is found.  The comparison for the use_id is to prevent some
        # random directory named 'root' in the users path from being selected
        # as the real "root" directory.  Of course this only works if the
        # while uses an 'or' and not an 'and'.  Silly programmer error...
        # Grrrrrrr.
        name = "dummy"  # compoent name of a directory.
        #while name != "root" or use_id != "000000000000000000001020":
        while use_id != "000000000000000000000000000000000000":
            #print "name [1]:", name
            use_id = self._get_parent(use_id, search_path) #get parent id
            #print "use_id:", use_id
            name = self._get_nameof(use_id, search_path) #get nameof parent id
            #print "name [2]:", name
            filepath = os.path.join(name, filepath) #join filepath together
            #print "filepath:", filepath
        filepath = os.path.join("/", filepath)
        #print "filepath [3]:", filepath
        #print "filepath.split(/):", filepath.split("/")
        filepath = string.join((filepath.split("/")[2:]), "/")
        #print "filepath [4]:", filepath
        #Truncate the begining false directories.
        #if filepath[:13] == "/root/fs/usr/":
        #    filepath = filepath[13:]
        #else:
        #    raise OSError(errno.ENOENT, "%s: %s" % (os.strerror(errno.ENOENT),
        #                                            "Not a valid pnfs id"))

        #Munge the mount point and the directories.  First check if the two
        # paths can be munged without modification.
        if file_utils.e_access(os.path.join(search_path, filepath), os.F_OK):
            filepath = os.path.join(search_path, filepath)
        #Then check if removing the last compenent of the mount point path
        # (search_path) will help when munged.
        elif file_utils.e_access(
            os.path.join(os.path.dirname(search_path), filepath), os.F_OK):
            filepath = os.path.join(os.path.dirname(search_path), filepath)
        #Lastly, remove the first entry in the file path before munging.
        elif file_utils.e_access(
            os.path.join(search_path, filepath.split("/", 1)[1]), os.F_OK):
          filepath = os.path.join(search_path, filepath.split("/", 1)[1])
        #If the path is "/pnfs/fs" try inserting "usr".
        elif os.path.basename(search_path) == "fs" and \
             file_utils.e_access(os.path.join(search_path, "usr", filepath),
                                 os.F_OK):
            filepath = os.path.join(search_path, "usr", filepath)
        else:
            #One last thing to try, if an admin path is found, try it.
            for amp in get_enstore_admin_mount_point(): #amp = Admin Mount Path
                try_path = os.path.join(amp, filepath)
                if file_utils.e_access(try_path, os.F_OK):
                    filepath = try_path
                    break
            else:
                #If we get here then a mount point exists that belongs to
                # a pnfs server that knows about the file, but it is not the
                # correct mount point.  Instead of returning:
                #   /pnfs/flake/encp_test/100KB_002
                # you would get
                #   flake/encp_test/100KB_002
                pass

        if not id:
            self.path = filepath

        return filepath




    ##########################################################################

    #Return just the mount point section of a pnfs path.
    def get_mount_point(self, filepath = None):
        if filepath:
            fname = filepath
        else:
            fname = self.filepath
        mData = parse_mtab()
        for aKey in mData.keys():
            mPoint = mData[aKey][1]
            if mPoint in fname:
                return mPoint
        return None

        
    def get_pnfs_db_directory(self, filepath = None):
        if filepath:
            fname = filepath
        else:
            fname = self.filepath

        #Check if we can get the database directly.  This only works
        # for directories.
        try:
            initial_pnfs_database = self.get_database(fname).strip()
            current_path = old_path = fname
        except (OSError, IOError), msg:
            #If we need a directory, get it and try again.
            if msg.args[0] == errno.ENOTDIR:
                try:
                    dname = get_directory_name(fname)
                    initial_pnfs_database = self.get_database(dname).strip()
                    current_path = old_path = dname
                except (OSError, IOError):
                    raise sys.exc_info()[0], sys.exc_info()[1], \
                          sys.exc_info()[2]
            else:
                raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]

        #Strip off one directory segment at a time.  We are looking for
        # where the DB number changes.
        while 1:
            if is_access_name(current_path):
                current_path = get_directory_name(current_path)
            else:
                current_path = os.path.dirname(current_path)
            try:
                current_pnfs_database = self.get_database(current_path).strip()
            except (OSError, IOError), msg:
                if msg.args[0] in [errno.ENOENT]:
                    #We found the mount point.
                    return old_path
                raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]

            if initial_pnfs_database != current_pnfs_database:
                #We found the change of DB.
                return old_path
            old_path = current_path

        return None

    #Get the mountpoint for the pnfs id.
    # As a side effect also get the first
    # 'id' is the pnfs id
    
    def _get_mount_point2(self, id, directory, pnfsname=None,
                          return_all = False):
        if id != None:
            if not is_chimeraid(id):
                raise ValueError("The pnfs id (%s) is not valid." % id)
        else:
            raise ValueError("No valid pnfs id.")


        #Try and optimize things by looking for the target to begin with.
        if type(pnfsname) == types.StringType:
            use_pnfsname = pnfsname % id
        else:
            use_pnfsname = ".(access)(%s)" % id

        #We will need the pnfs database numbers.
        use_pnfsid_db=int(id[:4], 16)

        #Try the initial directory.
        pfn = os.path.join(directory, use_pnfsname)
        try:
            #If the mountpoint doesn't know about our database fail now.
            """
            try:
                N(use_pnfsid_db, directory).get_databaseN(use_pnfsid_db)
            except (OSError, IOError), msg:
                if msg.args[0] == errno.ENOTDIR:
                    #This can/will happen if the pnfsid is for a tag file.
                    # The parent of a tag is not a directory, but is
                    # another tag, hence ENOTDIR.
                    raise OSError(errno.ENOTDIR, "Force PNFS search")
                else:
                    raise OSError(errno.ENOENT, "Force PNFS search")
            """
            #
            # Get the requested information from PNFS.
            #
            f = file_utils.open(pfn, 'r')
            if pfn.find("showid") > -1:
                pnfs_value = f.readlines()
            else:
                pnfs_value = f.readline()
            f.close()

            
            #Remember to truncate the original path to just the mount
            # point.
            search_path = self.get_mount_point(directory)
                    #found_db_num = int(self.get_database(search_path).split(":")[1],
            #                   16)

            #Small hack for the admin path.
            ## Hope that get_mount_point() always returns "/pnfs/fs"
            ## for the admin path.  If it were ever to return "/pnfs/fs/usr"
            ## this "Small hack for the admin path." will break.
            #if found_db_num == 0:
            #    search_path = os.path.join(search_path, "usr")

            mp_match_list = [search_path]
            pnfs_value_match_list = [pnfs_value]
            #print "mp_match_list:", mp_match_list
        except (OSError, IOError), msg:
            if is_nameof_name(pfn) and msg.args[0] == errno.EIO and \
               os.geteuid() != 0:
                #We don't have permission to obtain the information.
                raise OSError(errno.EACCES,
                              "%s: %s" % (os.strerror(errno.EACCES), pfn))
            #elif msg.args[0] in [errno.EACCES, errno.EPERM] and \
            #         os.geteuid() == 0:
            #    #If we found the non-admin path and are user root.
            #    pass
            elif msg.args[0] == errno.ENOTDIR:
                #We can legitly get here if the pnfs id is for a tag file.
                sfn = os.path.join(directory,
                                   ".(showid)(%s)" % id)
                f = file_utils.open(sfn, 'r')
                showid_value = f.readlines()
                f.close()

                for line in showid_value:
                    if line.find("Tag ( Inode )") != -1:
                        #Finding the "Tag ( Inode )" string means we have
                        # a pnfs id.
                        
                        #Remember to truncate the original path to just the
                        # mount point.
                        search_path = self.get_mount_point(directory)
                        
                        found_db_num = int(
                            self.get_database(search_path).split(":")[1],
                            16)

                        #Small hack for the admin path.
                        if found_db_num == 0:
                            search_path = os.path.join(search_path, "usr")

                        mp_match_list = [search_path]
                        pnfs_value_match_list = showid_value

                        #We need to return the match for the default
                        # directory (likely the CWD).
                        return mp_match_list, pnfs_value_match_list
            elif msg.args[0] != errno.ENOENT:
                raise OSError(msg.args[0],
                              "%s: %s" % (os.strerror(msg.args[0]), pfn))
            elif msg.args[0] == errno.ENOENT:
                #We need to determine if the file is orphaned.  At this
                # point the target returned "no such file or directory",
                # so if the parent returns successful here, then we know
                # it is a orphan.
                try:
                    parent_fn = os.path.join(directory, ".(parent)(%s)" % id)
                    parent_fp = file_utils.open(parent_fn, "r")
                    parent_id = parent_fp.readlines()
                    parent_fp.close()
                    if parent_id:
                        #orphaned file
                        raise OSError(errno.EBADFD,
                              "%s: orphaned file" % os.strerror(errno.EBADFD),
                                      pfn)
                except (OSError, IOError):
                    pass

            #Only ENOENT should be able to get here.
            
            count = 0
            found_db_num = None
            #found_fname = None
            found_db_info = None
            mp_match_list = []
            pnfs_value_match_list = []
            search_list = process_mtab()
            #Search all of the pnfs mountpoints that are mounted.
            for db_info, (db_num, mp) in search_list:
                #print "db_info, (db_num, mp):", db_info, (db_num, mp)
                
                #If the mountpoint doesn't know about our database fail now.
                try:
                    cur_db_info = N(db_num, mp).get_databaseN(use_pnfsid_db)
                except (OSError, IOError):
                    continue

                #If this is a top level PNFS db, we can jump to
                # the correct info.
                for search_db_info, (search_db_num, search_mp) in search_list:
                    if cur_db_info == search_db_info:
                        use_mp = search_mp
                        break
                else:
                    use_mp = mp

                #Check if the current mp knows about our specific pnfsid.
                if os.path.basename(use_mp) == "fs":
                    pfn = os.path.join(use_mp, "usr", use_pnfsname)
                else:
                    pfn = os.path.join(use_mp, use_pnfsname)
                try:
                    f = file_utils.open(pfn, 'r')
                    pnfs_value = f.readline()
                    f.close()

                    #Get the directory of the current file.
                    afn = self.convert_to_access(pfn)
                    afn_dir = get_directory_name(afn)

                    if count:
                        fn_db_info = self.get_database(afn_dir)
                        if fn_db_info == found_db_info:
                            #If we get here then we found two mountpoints
                            # that map to the same file.  Since these
                            # two paths point to the same file,
                            # we don't want to fail with the ENODEV
                            # error a little farthur down.  This
                            # will most likely occur with both the
                            # /pnfs/path and /pnfs/fs/usr/path
                            # mountpoints being mounted.
                            continue

                    #Determine the correct mount point.  Different pnfs
                    # database areas can respond for any database on
                    # the same machine.  The current one knows about the
                    # db we are looking for, now just need to find the
                    # correctly matching mount point.
                    ###db_dir = self.get_pnfs_db_directory(afn_dir)
                    db_dir = self.get_mount_point(afn_dir)
                    #The target_db_area step is necessary for databases like
                    # /pnfs/sdss/db2.  The if below handles things for
                    # locations like /pnfs/sdss.
                    target_db_area = get_directory_name(db_dir)
                    #db_db_info = self.get_database(target_db_area)
                    # This is a hack for chimera
                    # Instead of passing target_db_area which can be /pnfs we are passing db_dir which can be /pnfs/sekhri to get_database
                    # The get_database will return the correct key. All this is perhaps not needed
                    db_db_info = self.get_database(db_dir)
                    db_data = db_pnfsid_cache.get(db_db_info, None)
                    #Determine if we found the admin database (db_data[0] == 0)
                    # and we weren't explicitly looking for it
                    if db_data == None or \
                       (db_data != None and db_data[0] == 0 and db_num != 0):
                        #In the event we were looking for the a top level
                        # db (i.e. /pnfs/sdss) we want to not find the admin
                        # db (/pnfs/fs).  This means skipping the
                        # "target_db_area = get_directory_name(db_dir)"
                        # step above, which would erroneously give
                        # /pnfs/sdss/.(access)(000000000000000000001080)
                        # (aka /pnfs/fs/usr) as the database we are looking
                        # for.
                        #print "db_dir:", db_dir
                        db_db_info = self.get_database(db_dir)
                        db_data = db_pnfsid_cache.get(db_db_info, None)
                    if db_data != None:
                        if found_db_info != db_db_info:
                            #
                            # Set these three values to include the found item.
                            #
                            count = count + 1
                            mp_match_list.append(db_data[1])
                            pnfs_value_match_list.append(pnfs_value)

                            if count == 1:
                                #We just found the first one.  Remember this
                                # to avoid catching it again.
                                search_path = db_data[1]
                                found_db_num = db_data[0]
                                #found_fname = pfn
                                found_db_info = db_db_info
                except (OSError, IOError), msg:
                    if msg.args[0] in [errno.EIO, errno.ENOENT]:
                        #This block of code is to report if an orphaned file
                        # was requested.  This will only apply to orphans
                        # with their 'parent' directory missing them.
                        try:
                            if os.path.basename(use_mp) == "fs":
                                parent_fn = os.path.join(use_mp, "usr",
                                                   ".(parent)(%s)" % id)
                                showid_fn = os.path.join(use_mp, "usr",
                                                   ".(showid)(%s)" % id)
                            else:
                                parent_fn = os.path.join(use_mp,
                                                         ".(parent)(%s)" % id)
                                showid_fn = os.path.join(use_mp,
                                                         ".(showid)(%s)" % id)
                                
                            parent_fp = file_utils.open(parent_fn, "r")
                            parent_id = parent_fp.readlines()
                            parent_fp.close()

                            if parent_id:
                                #We can't close the book on this just yet.
                                # If the pnfsid is a tag, then we need to
                                # handle things special.
                                showid_fp = file_utils.open(showid_fn, "r")
                                showid_data = showid_fp.readlines()
                                showid_fp.close()
                                for line in showid_data:
                                    if line.find("Tag ( Inode )") != -1:
                                        #If we get here, then we determined
                                        # that the pnfs is a tag file pnfsid.

                                        #First, construct the access name of
                                        # the directory that this tag belongs
                                        # to.
                                        parent_id_clean = parent_id[0][:-1]
                                        afn_dir = os.path.join(
                                            use_mp,
                                            ".(access)(%s)" % parent_id_clean)
                                        
                                        #
                                        # Set these three values to include
                                        # the found item.
                                        #
                                        count = count + 1
                                        mp_match_list.append(afn_dir)
                                        pnfs_value_match_list.append(
                                            showid_data)
                                        
                                        if count == 1:
                                            #We just found the first one.
                                            # Remember this to avoid catching
                                            # it again.

                                            
                                            #Determine the correct mount
                                            # point.  Different pnfs database
                                            # areas can respond for any
                                            # database on the same machine.
                                            # The current one knows about the
                                            # db we are looking for, now just
                                            # need to find the correctly
                                            # matching mount point.
                                            db_dir = \
                                            self.get_pnfs_db_directory(afn_dir)
                                            #The target_db_area step is
                                            # necessary for databases like
                                            # /pnfs/sdss/db2.  The if below
                                            # handles things for locations
                                            # like /pnfs/sdss.
                                            target_db_area = \
                                              get_directory_name(db_dir)
                                            db_db_info = \
                                              self.get_database(target_db_area)
                                            db_data = \
                                              db_pnfsid_cache.get(db_db_info,
                                                                  None)
                                            
                                            
                                            #We just found the first one.
                                            # Remember this to avoid catching
                                            # it again.
                                            search_path = db_data[1]
                                            found_db_num = db_data[0]
                                            #found_fname = pfn
                                            found_db_info = db_db_info

                                        break

                                continue
                                
                        except (OSError, IOError), msg:
                            parent_id = None


                        if parent_id:
                            #orphaned file
                            raise OSError(errno.EBADFD,
                                          "%s: orphaned file" %
                                          os.strerror(errno.EBADFD), pfn)

                    continue

            if count == 0:
                raise OSError(errno.ENOENT,
                              "%s: %s" % (os.strerror(errno.ENOENT),
                                          "Not a valid pnfs id"))
            elif count > 1 and not return_all:
                raise OSError(errno.ENODEV,
                              "%s: %s" % (os.strerror(errno.ENODEV),
                                          "Too many matching mount points",),
                              mp_match_list)

        #The pnfs_value is put into a list becuase originally this
        # function used readlines().  However, for performance reasons,
        # readline() is a better choice.  Returning a list is just a
        # historical note from having used readlines() previously.
        return mp_match_list, pnfs_value_match_list

    ##########################################################################

    # get the cursor information
    def get_cursor(self, directory=None):

        if directory:
            fname = os.path.join(directory, ".(get)(cursor)")
        else:
            fname = os.path.join(self.dir, ".(get)(cursor)")

        f = file_utils.open(fname,'r')
        cursor = f.readlines()
        f.close()
        
        if not directory:
            self.cursor = cursor
        return cursor

    # get the cursor information
    def get_counters(self, directory=None):

        if directory:
            fname = os.path.join(directory, ".(get)(counters)")
        else:
            fname = os.path.join(self.dir, ".(get)(counters)")

        f=file_utils.open(fname,'r')
        counters = f.readlines()
        f.close()

        if not directory:
            self.counters = counters
        return counters

    # get the position information
    def get_position(self, directory=None):

        if directory:
            fname = os.path.join(directory, ".(get)(postion)")
        else:
            fname = os.path.join(self.dir, ".(get)(postion)")

        f=file_utils.open(fname,'r')
        position = f.readlines()
        f.close()

        if not directory:
            self.position = position
        return position

    # get the database information
    def get_database(self, directory=None):
        #return random data that is consistant with pnfs format
        if directory != None:
           val = (0, directory)
           to_return = find_key(db_pnfsid_cache, val)
           if to_return != None and len(to_return) > 0:
               #return to_return[0]
               return to_return
        return "admin:0:r:enabled:/srv2/pnfs/db/admin" 


    ##########################################################################

    def get_file_size(self, filepath=None):

        if filepath:
            fname = filepath
            #Get the file system size.
            os_filesize = long(file_utils.get_stat(fname)[stat.ST_SIZE])
        else:
            fname = self.filepath
            self.verify_existance()
            self.pstatinfo(update=0) #verify_existance does the os.stat().
            #Get the file system size.
            os_filesize = long(self.file_size)

        #If there is no layer 4, make sure an error occurs.
        try:
            pnfs_filesize = long(self.get_xreference(fname)[2].strip())
        except ValueError:
            pnfs_filesize = long(-1)
            #self.file_size = os_filesize
            #return os_filesize

        #Error checking.  However first ignore large file cases.
        if os_filesize == 1 and pnfs_filesize > long(2L**31L) - 1:
            if not filepath:
                self.file_size = pnfs_filesize
            return long(pnfs_filesize)
        #Make sure they are the same.
        elif os_filesize != pnfs_filesize:
            raise OSError(errno.EBADFD,
                     "%s: filesize corruption: OS size %s != PNFS size %s" % \
                      (os.strerror(errno.EBADFD), os_filesize, pnfs_filesize))

        if not filepath:
            self.file_size = os_filesize
        return long(os_filesize)
	

    def set_file_size(self, filesize, filepath=None):
        #handle large files.
        if filesize > (2**31L) - 1:
            size = 1
        else:
            size = filesize

        #xref = self.get_xreference()
        #formated_size = str(filesize)
        #if formated_size[-1] == "L":
        #    formated_size = formated_size[:-1]
        #xref[2] = formated_size  #get_xreferece() always returns a 10-tuple.
        #apply(self.set_xreference, xref) #Don't untuple xref.

        #Set the filesize that the filesystem knows about.
        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.filepath

        #Don't report the hidden file to the user if there is a problem,
        # report the original file.
        self.verify_existance(use_filepath)
        fname = self.fset_file(use_filepath, size)
        try:
            f = file_utils.open(fname,'w')
            f.close()
        except (OSError, IOError), msg:
            if msg.args[0] == errno.ENAMETOOLONG:
                Trace.log(e_errors.ERROR, "fset[1]")
                #If the .(fset) filename is too long for PNFS, then we need
                # to make a shorter temproary link to it and try it again.

                #First, using .(access)() paths access the directory for
                # the file stored in use_filepath.
                try_dir = self.get_pnfs_db_directory(use_filepath)
                try_dir_pnfsid = self.get_id(os.path.dirname(use_filepath))
                try_dir = self.access_file(try_dir, try_dir_pnfsid)

                Trace.log(e_errors.ERROR, "fset[2]")
                #Second, create the .(access)() path to the inode record
                # for the filename stored in use_filepath.
                try_pnfsid = self.get_id(use_filepath)
                try_path = self.access_file(try_dir, try_pnfsid)

                Trace.log(e_errors.ERROR, "fset[3]")
                #Third, create a new link name using the .(access)()
                # directory path.
                short_tmp_name = ".%s_%s" % (os.uname()[1], os.getpid())
                link_name = os.path.join(try_dir, short_tmp_name)

                Trace.log(e_errors.ERROR, "fset[4]")
                #Get the existing link count.
                link_count = file_utils.get_stat(try_path)[stat.ST_NLINK]

                Trace.log(e_errors.ERROR, "fset[5]")
                #Make the temporary link using the sorter name.
                try:
                    os.link(try_path, link_name)
                except (OSError, IOError), msg:
                    if msg.args[0] == errno.EEXIST \
                       and file_utils.get_stat(link_name)[stat.ST_NLINK] == link_count + 1:
                        # If the link count increased by one, we succeded
                        # even though there was an EEXIST error.  This
                        # situation can occur over NFS V2.
                        pass
                    else:
                        raise sys.exc_info()[0], sys.exc_info()[1], \
                              sys.exc_info()[2]

                Trace.log(e_errors.ERROR, "fset[6]")
                #Set the new file size.
                try:
                    fname = self.fset_file(link_name, size)
                    f = file_utils.open(fname, "w")
                    f.close()
                except (OSError, IOError), msg:
                    os.unlink(link_name)
                    raise sys.exc_info()[0], sys.exc_info()[1], \
                          sys.exc_info()[2]
                Trace.log(e_errors.ERROR, "fset[7]")
                #Cleanup the temporary link.
                os.unlink(link_name)

                Trace.log(e_errors.ERROR, "fset[8]")
            else:
                raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]

            Trace.log(e_errors.ERROR, "fset[9]")

        #Update the times.
        if filepath:
            self.utime(filepath)
        else:
            self.utime()
            self.pstatinfo()


    # set a new mode for the existing file
    def chmod(self, mode, filepath=None):
        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.pnfsFilename
            
        file_utils.chmod(use_filepath, mode)

        if filepath:
            self.utime(filepath)
        else:
            self.utime()
            self.pstatinfo()

    # change the ownership of the existing file
    def chown(self, uid, gid, filepath=None):
        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.pnfsFilename
        
        file_utils.chown(use_filepath, uid, gid)

        if filepath:
            self.utime(filepath)
        else:
            self.utime()
            self.pstatinfo()

    ##########################################################################

    # store a new bit file id
    def set_bit_file_id(self,value,filepath=None):
        if filepath:
            self.writelayer(enstore_constants.BFID_LAYER, value, filepath)
            self.get_bit_file_id(filepath)
        else:
            self.writelayer(enstore_constants.BFID_LAYER, value)
            self.get_bit_file_id()

        return value

    # store the cross-referencing data
    def set_xreference(self, volume, location_cookie, size, file_family,
                       pnfsFilename, volume_filepath, id, volume_fileP,
                       bit_file_id, drive, crc, filepath=None):

        value = (11*"%s\n")%(volume,
                             location_cookie,
                             size,
                             file_family,
                             pnfsFilename,
                             volume_filepath,
                             id,
                             volume_fileP,  #.id,
                             bit_file_id,
                             drive,
                             crc)
        
        Trace.trace(11,'value='+value)
        if filepath:
            self.writelayer(enstore_constants.XREF_LAYER, value, filepath)
            self.get_xreference(filepath)
        else:
            self.writelayer(enstore_constants.XREF_LAYER, value)
            self.get_xreference()

        return value
    
    # get the bit file id
    def get_bit_file_id(self, filepath=None):

        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.filepath
        
        try:
            bit_file_id = self.readlayer(enstore_constants.BFID_LAYER,
                                         use_filepath)[0]
        except IndexError:
            raise IOError(errno.EIO, "%s: Layer %d is empty: %s" %
                          (os.strerror(errno.EIO),
                           enstore_constants.BFID_LAYER,
                           use_filepath))
        except (OSError, IOError), msg:
            if msg.args[0] in (errno.ENOENT,):
                #We only need to re-create the ENOENT error.  If reading
                # layer 1 gives ENOENT, then the entire file is gone
                # (which is what we want to report).  However, most
                # (all?) other errors will apply to the layer 1 file
                # and should be reported as such.
                exception = sys.exc_info()[0]
                raise exception(msg.args[0], "%s: %s" % \
                                (os.strerror(msg.args[0]), use_filepath))
            else:
                #Just pass allong all other exceptions.
                raise sys.exc_info()[0], sys.exc_info()[1], \
                      sys.exc_info()[2]
            
        if not filepath:
            self.bit_file_id = bit_file_id

        return bit_file_id

    # get the cross reference layer
    def get_xreference(self, filepath=None):

        if filepath:
            use_filepath = filepath
        else:
            use_filepath = self.filepath

        #Get the xref layer information.
        xinfo = self.readlayer(enstore_constants.XREF_LAYER, use_filepath)
        if len(xinfo) == 0:
            raise IOError(errno.EIO, "%s: Layer %d is empty: %s" %
                          (os.strerror(errno.EIO),
                           enstore_constants.XREF_LAYER,
                           use_filepath))

        #Strip off whitespace from each line.
        xinfo = map(string.strip, xinfo[:11])
        #Make sure there are 11 elements.  Early versions only contain 9.
        # Some contain 10.  This prevents problems.
        xinfo = xinfo + ([UNKNOWN] * (11 - len(xinfo)))

        #If the class member value was used, store the values seperatly.
        if not filepath:
            try:
                self.volume = xinfo[0]
                self.location_cookie = xinfo[1]
                self.size = xinfo[2]
                self.origff = xinfo[3]
                self.origname = xinfo[4]
                self.mapfile = xinfo[5]
                self.pnfsid_file = xinfo[6]
                self.pnfsid_map = xinfo[7]
                self.bfid = xinfo[8]
                self.origdrive = xinfo[9]
                self.crc = xinfo[10]
            except ValueError:
                pass

            self.xref = xinfo

        return xinfo

    ##########################################################################

    # get the stat of file/directory
    def get_stat(self, filepath=None):

        #Get the xref layer information.
        if filepath:
            fname = filepath
        else:
            fname = self.filepath
         
        try :   
            # first the file itself
            pstat = file_utils.get_stat(fname)
            pstat = tuple(pstat)

            if not filepath:
                self.pstat = pstat
            return pstat
        except (OSError, IOError):
      
            """
            if msg.args[0] in [errno.ENOENT]:
                if is_layer_access_name(fname):
                    # remove the level from the fname
                    tmp_name = fname[: len(fname) - 3 ]
                    tmp = file_utils.get_stat(tmp_name)
                    return tmp
            raise sys.exc_info()[0], sys.exc_info()[1], \
                  sys.exc_info()[2]
            """
            pass
        return tuple(['','','','','','','',''])

    # get the stat of file/directory, or if non-existant, its directory
    def get_pnfsstat(self, filepath=None):

        #Get the xref layer information.
        if filepath:
            fname = filepath
        else:
            fname = self.filepath
            
        try:
            # first the file itself
            pstat = file_utils.get_stat(fname)
        except OSError, msg:
            # if that fails, try the directory
            try:
                pstat = file_utils.get_stat(get_directory_name(fname))
            except OSError:
                raise msg

        pstat = tuple(pstat)

        if not filepath:
            self.pstat = pstat

        return pstat

    # get the uid from the stat member
    def pstat_decode(self):
	self.uid = ERROR
        self.uname = UNKNOWN
        self.gid = ERROR
        self.gname = UNKNOWN
        self.mode = 0
        self.mode_octal = 0
        self.file_size = ERROR
        self.inode = 0
        #What these do, I do not know.  MWZ
        self.rmajor, self.rminor = (0, 0)
        self.major, self.minor = (0, 0)

        #In case the stat hasn't been done already, do it now.
        if not hasattr(self, "pstat"):
            self.get_stat()
        
        #Get the user id of the file's owner.
        try:
            self.uid = self.pstat[stat.ST_UID]
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

        #Get the user name of the file's owner.
        try:
            self.uname = pwd.getpwuid(self.uid)[0]
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

        #Get the group id of the file's owner.
        try:
            self.gid = self.pstat[stat.ST_GID]
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

        #Get the group name of the file's owner.
        try:
            self.gname = grp.getgrgid(self.gid)[0]
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

        #Get the file mode.
        try:
            # always return mode as if it were a file, not directory, so
            #  it can use used in enstore cpio creation  (we will be
            #  creating a file in this directory)
            # real mode is available in self.stat for people who need it
            self.mode = (self.pstat[stat.ST_MODE] % 0777) | 0100000
            self.mode_octal = str(oct(self.mode))
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            self.mode = 0
            self.mode_octal = 0

        #if os.path.exists(self.filepath):
        if stat.S_ISREG(self.pstat[stat.ST_MODE]):
            real_file = 1
        else:
            real_file = 0  #Should be the parent directory.

        #Get the file size.
        try:
            if real_file:    #os.path.exists(self.filepath):
                self.file_size = long(self.pstat[stat.ST_SIZE])
                if self.file_size == 1L:
                    self.file_size = long(self.get_xreference()[2]) #[2] = size
            else:
                try:
                    del self.file_size
                except AttributeError:
                    pass  #Was not present.
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

        #Get the file inode.
        try:
            if real_file:   #os.path.exists(self.filepath):
                self.inode = self.pstat[stat.ST_INO]
            else:
                try:
                    del self.inode
                except AttributeError:
                    pass #Was not present.
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

        #Get the major and minor device codes for the device the file
        # resides on.
        try:
            #code_dict = Devcodes.MajMin(self.pnfsFilename)
            #self.major = code_dict["Major"]
            #self.minor = code_dict["Minor"]
            
            #The following math logic was taken from
            # $ENSTORE_DIR/modules/Devcodes.c.  For performance reasons,
            # this was done in python.  It turns out to be slower to wait
            # for another stat() call in the C implimentation of Devcodes
            # than using the existing stat info implemented in python.
            # This is largly due to pnfs responce delays.
            self.major = int(((self.pstat[stat.ST_DEV]) >> 8) & 0xff)
            self.minor = int((self.pstat[stat.ST_DEV]) & 0xff)
        except (KeyboardInterrupt, SystemExit):
            raise sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
        except:
            pass

    # update all the stat info on the file, or if non-existent, its directory
    def pstatinfo(self, update=1):
        #Get new stat() information if requested.
        if update:
            self.get_pnfsstat()

        #Set various class values.
        self.pstat_decode()

##############################################################################

    #Prints out the specified layer of the specified file.
    def player(self, intf):
        try:
            self.verify_existance()
            data = self.readlayer(intf.named_layer)
            for datum in data:
                print datum.strip()
            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1

    #For legacy purposes.
    pcat = player
    
    #Snag the cross reference of the file inside self.file.
    #***LAYER 4**
    def pxref(self):  #, intf):
        names = ["volume", "location_cookie", "size", "file_family",
                 "original_name", "map_file", "pnfsid_file", "pnfsid_map",
                 "bfid", "origdrive", "crc"]
        try:
            self.verify_existance()
            data = self.get_xreference()
            #With the data stored in lists, with corresponding values
            # based on the index, then just print them out.
            for i in range(len(names)):
                print "%s: %s" % (names[i], data[i])
            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1
        
    #For legacy purposes.
    pinfo = pxref

    #Prints out the bfid value for the specified file.
    #***LAYER 1***
    def pbfid(self):  #, intf):
        try:
            self.verify_existance()
            self.get_bit_file_id()
            print self.bit_file_id
            return 0
        except IndexError:
            print UNKNOWN
            return 1
        except (IOError, OSError), detail:
            print str(detail)
            return 1

    #Print out the filesize of the file from this layer.  It should only
    # be here as long as pnfs does not support NFS ver 3 and the filesize
    # is longer than 2GB.
    #***LAYER 4***
    def pfilesize(self):  #, intf):
        try:
            self.get_file_size()
            print self.file_size
            return 0
        except (OSError, IOError), detail:
            """
            try:
                # Get layer 2 when layer 4 is not available.
                data = self.readlayer(2)
                # Define the match/search once before the loop.
                size_match = re.compile("l=[0-9]+")
                #Loop over the data in layer 2 looking for the length value.
                for line in data:
                    result = size_match.search(line)
                    if result != None:
                        
                        #Found the length value.
                        result = result.group()[2:] #Remove the "l=".
                        pnfs_filesize = long(result)

                        #Get the os size.  os.stat() should have been called
                        # in get_file_size().
                        try:
                            os_filesize = long(self.pstat[stat.ST_SIZE])
                        except (TypeError, AttributeError):
                            raise detail

                        #Handle the case where the sizes match or the file
                        # is a large file.
                        if pnfs_filesize == os_filesize or \
                               (os_filesize == 1L and
                                pnfs_filesize > long(2L**31L) - 1):
                            print pnfs_filesize
                            return 0
                        #Handle the case where the sizes do not match.
                        else:
                            print "%s: filesize corruption: " \
                                      "OS size %s != PNFS L2 size %s" % \
                                      (os.strerror(errno.EBADFD),
                                       os_filesize, pnfs_filesize)
                            return 1

                        #Should never get here.
                        break
            except (IOError, OSError, TypeError, AttributeError):
                #There is no layer 2 to check.
                pass
            """
            
            print str(detail)
            return 1

##############################################################################

    def pls(self, intf):
        filename = self.use_file(self.filepath, int(intf.named_layer))
        os.system("ls -alsF \"%s\"" % filename)
        
    def pecho(self, intf):
        try:
            self.writelayer(intf.named_layer, intf.text)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        
    def prm(self, intf):
        try:
            self.writelayer(intf.named_layer, "")
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1

    def pcp(self, intf):
        try:
            f = file_utils.open(intf.unixfile, 'r')

            data = f.readlines()
            file_data_as_string = ""
            for line in data:
                file_data_as_string = file_data_as_string + line

            f.close()

            self.writelayer(intf.named_layer, file_data_as_string)

            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1

    def psize(self, intf):
        try:
            self.set_file_size(intf.filesize)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
    
    def pio(self):  #, intf):
        print "Feature not yet implemented."

        #fname = "%s/.(fset)(%s)(io)(on)" % (self.dir, self.file)
        #os.system("touch" + fname)
    
    def pid(self):  #, intf):
        try:
            self.get_id()
            print_results(self.id)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        
    def pshowid(self):  #, intf):
        try:
            self.get_showid()
            print_results(self.showid)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        except (AttributeError, ValueError), detail:
            sys.stderr.write("A valid pnfs id was not entered.\n")
            return 1
    
    def pconst(self):  #, intf):
        try:
            self.get_const()
            print_results(self.const)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        
    def pnameof(self):  #, intf):
        try:
            self.get_nameof()
            print_results(self.nameof)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        except (AttributeError, ValueError), detail:
            sys.stderr.write("A valid pnfs id was not entered.\n")
            return 1
        
    def ppath(self):  #, intf):
        try:
            rtn_results = self.get_path()
            print_results2(rtn_results)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            if detail.args[0] in [errno.ENODEV]:
                print_results2(detail.filename)
            return 1
        except (AttributeError, ValueError), detail:
            print detail
            sys.stderr.write("A valid pnfs id was not entered.\n")
            return 1

    def pmount_point(self):
        try:
            print_results(self.get_mount_point())
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        except (AttributeError, ValueError), detail:
            sys.stderr.write("A valid pnfs id was not entered.\n")
            return 1
        
    def pparent(self):  #, intf):
        try:
            self.get_parent()
            print_results(self.parent)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        except (AttributeError, ValueError), detail:
            sys.stderr.write("A valid pnfs id was not entered.\n")
            return 1
    
    def pcounters(self):  #, intf):
        try:
            self.get_counters()
            print_results(self.counters)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        
    def pcursor(self):  #, intf):
        try:
            self.get_cursor()
            print_results(self.cursor)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
            
    def pposition(self):  #, intf):
        try:
            self.get_position()
            print_results(self.position)
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1
        
    def pdatabase(self, intf):
        try:
            print_results(self.get_database(intf.file))
            return 0
        except (OSError, IOError), detail:
            sys.stderr.write("%s\n" % str(detail))
            return 1


    def pdown(self, intf):
        if os.environ['USER'] != "root":
            print "must be root to create enstore system-down wormhole"
            return
        
        dname = "/pnfs/fs/admin/etc/config/flags"
        if not os.access(dname, os.F_OK | os.R_OK):
            print "/pnfs/fs is not mounted"
            return

        fname = "/pnfs/fs/admin/etc/config/flags/disabled"
        f = file_utils.open(fname,'w')
        f.write(intf.reason)
        f.close()

        os.system("touch .(fset)(disabled)(io)(on)")
        
    def pup(self):  #, intf):
        if os.environ['USER'] != "root":
            print "must be root to create enstore system-down wormhole"
            return
        
        dname = "/pnfs/fs/admin/etc/config/flags"
        if not os.access(dname, os.F_OK | os.R_OK):
            print "/pnfs/fs is not mounted"
            return

        os.remove("/pnfs/fs/admin/etc/config/flags/disabled")

    def pdump(self):  #, intf):
        self.dump()

##############################################################################

class PnfsInterface(option.Interface):

    def __init__(self, args=sys.argv, user_mode=1):
        # fill in the defaults for the possible options
        #self.test = 0
        #self.status = 0
        #self.info = 0
        #self.file = ""
        #self.restore = 0
        #These my be used, they may not.
        #self.duplicate_file = None
        option.Interface.__init__(self, args=args, user_mode=user_mode)

    pnfs_user_options = {
        option.BFID:{option.HELP_STRING:"lists the bit file id for file",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"bfid",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_NAME:"file",
                     option.VALUE_TYPE:option.STRING,
                     option.VALUE_USAGE:option.REQUIRED,
                     option.VALUE_LABEL:"filename",
                     option.FORCE_SET_DEFAULT:option.FORCE,
		     option.USER_LEVEL:option.USER
                     },
        option.CAT:{option.HELP_STRING:"see --layer",
                    option.DEFAULT_VALUE:option.DEFAULT,
                    option.DEFAULT_NAME:"layer",
                    option.DEFAULT_TYPE:option.INTEGER,
                    option.VALUE_NAME:"file",
                    option.VALUE_TYPE:option.STRING,
                    option.VALUE_USAGE:option.REQUIRED,
                    option.VALUE_LABEL:"filename",
                    option.FORCE_SET_DEFAULT:option.FORCE,
                    option.USER_LEVEL:option.USER,
                    option.EXTRA_VALUES:[{option.DEFAULT_VALUE:option.DEFAULT,
                                          option.DEFAULT_NAME:"named_layer",
                                          option.DEFAULT_TYPE:option.INTEGER,
                                          option.VALUE_NAME:"named_layer",
                                          option.VALUE_TYPE:option.INTEGER,
                                          option.VALUE_USAGE:option.OPTIONAL,
                                          option.VALUE_LABEL:"layer",
                                          }]
                    },
        option.DUPLICATE:{option.HELP_STRING:"gets/sets duplicate file values",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"duplicate",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_USAGE:option.IGNORED,
		     option.USER_LEVEL:option.ADMIN,
                     option.EXTRA_VALUES:[{option.DEFAULT_VALUE:"",
                                           option.DEFAULT_NAME:"file",
                                           option.DEFAULT_TYPE:option.STRING,
                                           option.VALUE_NAME:"file",
                                           option.VALUE_TYPE:option.STRING,
                                           option.VALUE_USAGE:option.OPTIONAL,
                                           option.VALUE_LABEL:"filename",
                                         option.FORCE_SET_DEFAULT:option.FORCE,
                                           },
                                          {option.DEFAULT_VALUE:"",
                                          option.DEFAULT_NAME:"duplicate_file",
                                           option.DEFAULT_TYPE:option.STRING,
                                           option.VALUE_NAME:"duplicat_file",
                                           option.VALUE_TYPE:option.STRING,
                                           option.VALUE_USAGE:option.OPTIONAL,
                                       option.VALUE_LABEL:"duplicate_filename",
                                         option.FORCE_SET_DEFAULT:option.FORCE,
                                           },]
                     },
        #option.ENSTORE_STATE:{option.HELP_STRING:"lists whether enstore " \
        #                                         "is still alive",
        #                 option.DEFAULT_VALUE:option.DEFAULT,
        #                 option.DEFAULT_NAME:"enstore_state",
        #                 option.DEFAULT_TYPE:option.INTEGER,
        #                 option.VALUE_NAME:"directory",
        #                 option.VALUE_TYPE:option.STRING,
        #                 option.VALUE_USAGE:option.REQUIRED,
        #                 option.USER_LEVEL:option.USER,
        #                 option.FORCE_SET_DEFAULT:option.FORCE,
        #             },
        option.FILE_FAMILY:{option.HELP_STRING: \
                            "gets file family tag, default; "
                            "sets file family tag, optional",
                            option.DEFAULT_VALUE:option.DEFAULT,
                            option.DEFAULT_NAME:"file_family",
                            option.DEFAULT_TYPE:option.INTEGER,
                            option.VALUE_TYPE:option.STRING,
                            option.USER_LEVEL:option.USER,
                            option.VALUE_USAGE:option.OPTIONAL,
                   },
        option.FILE_FAMILY_WIDTH:{option.HELP_STRING: \
                                  "gets file family width tag, default; "
                                  "sets file family width tag, optional",
                                  option.DEFAULT_VALUE:option.DEFAULT,
                                  option.DEFAULT_NAME:"file_family_width",
                                  option.DEFAULT_TYPE:option.INTEGER,
                                  option.VALUE_TYPE:option.STRING,
                                  option.USER_LEVEL:option.USER,
                                  option.VALUE_USAGE:option.OPTIONAL,
                   },
        option.FILE_FAMILY_WRAPPER:{option.HELP_STRING: \
                                    "gets file family wrapper tag, default; "
                                    "sets file family wrapper tag, optional",
                                    option.DEFAULT_VALUE:option.DEFAULT,
                                    option.DEFAULT_NAME:"file_family_wrapper",
                                    option.DEFAULT_TYPE:option.INTEGER,
                                    option.VALUE_TYPE:option.STRING,
                                    option.USER_LEVEL:option.USER,
                                    option.VALUE_USAGE:option.OPTIONAL,
                   },
	option.FILESIZE:{option.HELP_STRING:"print out real filesize",
			 option.VALUE_NAME:"file",
			 option.VALUE_TYPE:option.STRING,
			 option.VALUE_LABEL:"file",
                         option.USER_LEVEL:option.USER,
			 option.VALUE_USAGE:option.REQUIRED,
			 },
        option.INFO:{option.HELP_STRING:"see --xref",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"xref",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_NAME:"file",
                     option.VALUE_TYPE:option.STRING,
                     option.VALUE_USAGE:option.REQUIRED,
                     option.VALUE_LABEL:"filename",
                     option.USER_LEVEL:option.USER,
                     option.FORCE_SET_DEFAULT:option.FORCE,
                },
        option.LAYER:{option.HELP_STRING:"lists the layer of the file",
                      option.DEFAULT_VALUE:option.DEFAULT,
                      option.DEFAULT_NAME:"layer",
                      option.DEFAULT_TYPE:option.INTEGER,
                      option.VALUE_NAME:"file",
                      option.VALUE_TYPE:option.STRING,
                      option.VALUE_USAGE:option.REQUIRED,
                      option.VALUE_LABEL:"filename",
                      option.FORCE_SET_DEFAULT:option.FORCE,
                      option.USER_LEVEL:option.USER,
                      option.EXTRA_VALUES:[{option.DEFAULT_VALUE:
                                                                option.DEFAULT,
                                            option.DEFAULT_NAME:"named_layer",
                                            option.DEFAULT_TYPE:option.INTEGER,
                                            option.VALUE_NAME:"named_layer",
                                            option.VALUE_TYPE:option.INTEGER,
                                            option.VALUE_USAGE:option.OPTIONAL,
                                            option.VALUE_LABEL:"layer",
                                            }]
                 },
        option.LIBRARY:{option.HELP_STRING:"gets library tag, default; " \
                                      "sets library tag, optional",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"library",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_TYPE:option.STRING,
                   option.USER_LEVEL:option.USER,
                   option.VALUE_USAGE:option.OPTIONAL,
                   },
        #option.PNFS_STATE:{option.HELP_STRING:"lists whether pnfs is " \
        #                                      "still alive",
        #              option.DEFAULT_VALUE:option.DEFAULT,
        #              option.DEFAULT_NAME:"pnfs_state",
        #              option.DEFAULT_TYPE:option.INTEGER,
        #              option.VALUE_NAME:"directory",
        #              option.VALUE_TYPE:option.STRING,
        #              option.VALUE_USAGE:option.REQUIRED,
        #              option.USER_LEVEL:option.USER,
        #              option.FORCE_SET_DEFAULT:option.FORCE,
        #              },
        option.STORAGE_GROUP:{option.HELP_STRING:"gets storage group tag, " \
                              "default; sets storage group tag, optional",
                         option.DEFAULT_VALUE:option.DEFAULT,
                         option.DEFAULT_NAME:"storage_group",
                         option.DEFAULT_TYPE:option.INTEGER,
                         option.VALUE_TYPE:option.STRING,
                         option.USER_LEVEL:option.ADMIN,
                         option.VALUE_USAGE:option.OPTIONAL,
                   },
        option.TAG:{option.HELP_STRING:"lists the tag of the directory",
                    option.DEFAULT_VALUE:option.DEFAULT,
                    option.DEFAULT_NAME:"tag",
                    option.DEFAULT_TYPE:option.INTEGER,
                    option.VALUE_NAME:"named_tag",
                    option.VALUE_TYPE:option.STRING,
                    option.VALUE_USAGE:option.REQUIRED,
                    option.VALUE_LABEL:"tag",
                    option.FORCE_SET_DEFAULT:1,
                    option.USER_LEVEL:option.USER,
                    option.EXTRA_VALUES:[{option.DEFAULT_VALUE:"",
                                          option.DEFAULT_NAME:"directory",
                                          option.DEFAULT_TYPE:option.STRING,
                                          option.VALUE_NAME:"directory",
                                          option.VALUE_TYPE:option.STRING,
                                          option.VALUE_USAGE:option.OPTIONAL,
                                         option.FORCE_SET_DEFAULT:option.FORCE,
                                          }]
               },
        option.TAGCHMOD:{option.HELP_STRING:"changes the permissions"
                         " for the tag; use UNIX chmod style permissions",
                         option.DEFAULT_VALUE:option.DEFAULT,
                         option.DEFAULT_NAME:"tagchmod",
                         option.DEFAULT_TYPE:option.INTEGER,
                         option.VALUE_NAME:"permissions",
                         option.VALUE_TYPE:option.STRING,
                         option.VALUE_USAGE:option.REQUIRED,
                         option.FORCE_SET_DEFAULT:option.FORCE,
                         option.USER_LEVEL:option.USER,
                         option.EXTRA_VALUES:[{option.VALUE_NAME:"named_tag",
                                            option.VALUE_TYPE:option.STRING,
                                            option.VALUE_USAGE:option.REQUIRED,
                                            option.VALUE_LABEL:"tag",
                                              },]
                         },
        option.TAGCHOWN:{option.HELP_STRING:"changes the ownership"
                         " for the tag; OWNER can be 'owner' or 'owner.group'",
                         option.DEFAULT_VALUE:option.DEFAULT,
                         option.DEFAULT_NAME:"tagchown",
                         option.DEFAULT_TYPE:option.INTEGER,
                         option.VALUE_NAME:"owner",
                         option.VALUE_TYPE:option.STRING,
                         option.VALUE_USAGE:option.REQUIRED,
                         option.FORCE_SET_DEFAULT:option.FORCE,
                         option.USER_LEVEL:option.USER,
                         option.EXTRA_VALUES:[{option.VALUE_NAME:"named_tag",
                                            option.VALUE_TYPE:option.STRING,
                                            option.VALUE_USAGE:option.REQUIRED,
                                            option.VALUE_LABEL:"tag",
                                              },]
                         },
        option.TAGS:{option.HELP_STRING:"lists tag values and permissions",
                option.DEFAULT_VALUE:option.DEFAULT,
                option.DEFAULT_NAME:"tags",
                option.DEFAULT_TYPE:option.INTEGER,
                option.VALUE_USAGE:option.IGNORED,
                option.USER_LEVEL:option.USER,
                option.EXTRA_VALUES:[{option.DEFAULT_VALUE:"",
                                      option.DEFAULT_NAME:"directory",
                                      option.DEFAULT_TYPE:option.STRING,
                                      option.VALUE_NAME:"directory",
                                      option.VALUE_TYPE:option.STRING,
                                      option.VALUE_USAGE:option.OPTIONAL,
                                      option.FORCE_SET_DEFAULT:option.FORCE,
                                      }]
                },
        option.XREF:{option.HELP_STRING:"lists the cross reference " \
                                        "data for file",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"xref",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_NAME:"file",
                     option.VALUE_TYPE:option.STRING,
                     option.VALUE_USAGE:option.REQUIRED,
                     option.VALUE_LABEL:"filename",
                     option.USER_LEVEL:option.USER,
                     option.FORCE_SET_DEFAULT:option.FORCE,
                },
        }

    pnfs_admin_options = {
        option.CP:{option.HELP_STRING:"echos text to named layer of the file",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"cp",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_NAME:"unixfile",
                   option.VALUE_TYPE:option.STRING,
                   option.VALUE_USAGE:option.REQUIRED,
                   option.FORCE_SET_DEFAULT:option.FORCE,
                   option.USER_LEVEL:option.ADMIN,
                   option.EXTRA_VALUES:[{option.VALUE_NAME:"file",
                                         option.VALUE_TYPE:option.STRING,
                                         option.VALUE_USAGE:option.REQUIRED,
                                         option.VALUE_LABEL:"filename",
                                         },
                                        {option.VALUE_NAME:"named_layer",
                                         option.VALUE_TYPE:option.INTEGER,
                                         option.VALUE_USAGE:option.REQUIRED,
                                         option.VALUE_LABEL:"layer",
                                         },]
                   },
        option.CONST:{option.HELP_STRING:"",
                      option.DEFAULT_VALUE:option.DEFAULT,
                      option.DEFAULT_NAME:"const",
                      option.DEFAULT_TYPE:option.INTEGER,
                      option.VALUE_NAME:"file",
                      option.VALUE_TYPE:option.STRING,
                      option.VALUE_USAGE:option.REQUIRED,
                      option.VALUE_LABEL:"filename",
                      option.FORCE_SET_DEFAULT:option.FORCE,
                      option.USER_LEVEL:option.ADMIN,
                      },
        option.COUNTERS:{option.HELP_STRING:"",
                         option.DEFAULT_VALUE:option.DEFAULT,
                         option.DEFAULT_NAME:"counters",
                         option.DEFAULT_TYPE:option.INTEGER,
                         option.VALUE_NAME:"file",
                         option.VALUE_TYPE:option.STRING,
                         option.VALUE_USAGE:option.REQUIRED,
                         option.VALUE_LABEL:"filename",
                         option.FORCE_SET_DEFAULT:option.FORCE,
                         option.USER_LEVEL:option.ADMIN,
                         },
        option.COUNTERSN:{option.HELP_STRING:"(must have cwd in pnfs)",
                          option.DEFAULT_VALUE:option.DEFAULT,
                          option.DEFAULT_NAME:"countersN",
                          option.DEFAULT_TYPE:option.INTEGER,
                          option.VALUE_NAME:"dbnum",
                          option.VALUE_TYPE:option.STRING,
                          option.VALUE_USAGE:option.REQUIRED,
                          option.FORCE_SET_DEFAULT:option.FORCE,
                          option.USER_LEVEL:option.ADMIN,
                          },
        option.CURSOR:{option.HELP_STRING:"",
                       option.DEFAULT_VALUE:option.DEFAULT,
                       option.DEFAULT_NAME:"cursor",
                       option.DEFAULT_TYPE:option.INTEGER,
                       option.VALUE_NAME:"file",
                       option.VALUE_TYPE:option.STRING,
                       option.VALUE_USAGE:option.REQUIRED,
                       option.VALUE_LABEL:"filename",
                       option.FORCE_SET_DEFAULT:option.FORCE,
                       option.USER_LEVEL:option.ADMIN,
                       },
        option.DATABASE:{option.HELP_STRING:"",
                         option.DEFAULT_VALUE:option.DEFAULT,
                         option.DEFAULT_NAME:"database",
                         option.DEFAULT_TYPE:option.INTEGER,
                         option.VALUE_NAME:"file",
                         option.VALUE_TYPE:option.STRING,
                         option.VALUE_USAGE:option.REQUIRED,
                         option.VALUE_LABEL:"filename",
                         option.FORCE_SET_DEFAULT:option.FORCE,
                         option.USER_LEVEL:option.ADMIN,
                         },
        option.DATABASEN:{option.HELP_STRING:"(must have cwd in pnfs)",
                          option.DEFAULT_VALUE:option.DEFAULT,
                          option.DEFAULT_NAME:"databaseN",
                          option.DEFAULT_TYPE:option.INTEGER,
                          option.VALUE_NAME:"dbnum",
                          option.VALUE_TYPE:option.STRING,
                          option.VALUE_USAGE:option.REQUIRED,
                          option.FORCE_SET_DEFAULT:option.FORCE,
                          option.USER_LEVEL:option.ADMIN,
                          },
        option.DOWN:{option.HELP_STRING:"creates enstore system-down " \
                                        "wormhole to prevent transfers",
                option.DEFAULT_VALUE:option.DEFAULT,
                option.DEFAULT_NAME:"down",
                option.DEFAULT_TYPE:option.INTEGER,
                option.VALUE_NAME:"reason",
                option.VALUE_TYPE:option.STRING,
                option.VALUE_USAGE:option.REQUIRED,
                option.FORCE_SET_DEFAULT:option.FORCE,
                option.USER_LEVEL:option.ADMIN,
                },
        option.DUMP:{option.HELP_STRING:"dumps info",
              option.DEFAULT_VALUE:option.DEFAULT,
              option.DEFAULT_NAME:"dump",
              option.DEFAULT_TYPE:option.INTEGER,
              option.VALUE_USAGE:option.IGNORED,
              option.USER_LEVEL:option.ADMIN,
              },
        option.ECHO:{option.HELP_STRING:"sets text to named layer of the file",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"echo",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_NAME:"text",
                     option.VALUE_TYPE:option.STRING,
                     option.VALUE_USAGE:option.REQUIRED,
                     option.FORCE_SET_DEFAULT:option.FORCE,
                     option.USER_LEVEL:option.ADMIN,
                     option.EXTRA_VALUES:[{option.VALUE_NAME:"file",
                                           option.VALUE_TYPE:option.STRING,
                                           option.VALUE_USAGE:option.REQUIRED,
                                           option.VALUE_LABEL:"filename",
                                           },
                                          {option.VALUE_NAME:"named_layer",
                                           option.VALUE_TYPE:option.INTEGER,
                                           option.VALUE_USAGE:option.REQUIRED,
                                           option.VALUE_LABEL:"layer",
                                           },]
                },
        option.ID:{option.HELP_STRING:"prints the pnfs id",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"id",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_NAME:"file",
                   option.VALUE_TYPE:option.STRING,
                   option.VALUE_USAGE:option.REQUIRED,
                   option.VALUE_LABEL:"filename",
                   option.FORCE_SET_DEFAULT:option.FORCE,
                   option.USER_LEVEL:option.ADMIN,
              },
        option.IO:{option.HELP_STRING:"sets io mode (can't clear it easily)",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"io",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_NAME:"file",
                   option.VALUE_TYPE:option.STRING,
                   option.VALUE_USAGE:option.REQUIRED,
                   option.VALUE_LABEL:"filename",
                   option.FORCE_SET_DEFAULT:option.FORCE,
                   option.USER_LEVEL:option.ADMIN,
                   },
        option.LS:{option.HELP_STRING:"does an ls on the named layer " \
                                      "in the file",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"ls",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_NAME:"file",
                   option.VALUE_TYPE:option.STRING,
                   option.VALUE_USAGE:option.REQUIRED,
                   option.VALUE_LABEL:"filename",
                   option.FORCE_SET_DEFAULT:option.FORCE,
                   option.USER_LEVEL:option.ADMIN,
                   option.EXTRA_VALUES:[{option.DEFAULT_VALUE:option.DEFAULT,
                                         option.DEFAULT_NAME:"named_layer",
                                         option.DEFAULT_TYPE:option.INTEGER,
                                         option.VALUE_NAME:"named_layer",
                                         option.VALUE_TYPE:option.STRING,
                                         option.VALUE_USAGE:option.OPTIONAL,
                                         option.VALUE_LABEL:"layer",
                                         }]
              },
        option.MOUNT_POINT:{option.HELP_STRING:"prints the mount point of " \
                            "the pnfs file or directory",
                            option.DEFAULT_VALUE:option.DEFAULT,
                            option.DEFAULT_NAME:"mount_point",
                            option.DEFAULT_TYPE:option.INTEGER,
                            option.VALUE_NAME:"file",
                            option.VALUE_TYPE:option.STRING,
                            option.VALUE_USAGE:option.REQUIRED,
                            option.VALUE_LABEL:"filename",
                            option.FORCE_SET_DEFAULT:option.FORCE,
                            option.USER_LEVEL:option.ADMIN,
                            },
        option.NAMEOF:{option.HELP_STRING:"prints the filename of the pnfs id"\
                       " (CWD must be under /pnfs)",
                       option.DEFAULT_VALUE:option.DEFAULT,
                       option.DEFAULT_NAME:"nameof",
                       option.DEFAULT_TYPE:option.INTEGER,
                       option.VALUE_NAME:"pnfs_id",
                       option.VALUE_TYPE:option.STRING,
                       option.VALUE_USAGE:option.REQUIRED,
                       option.FORCE_SET_DEFAULT:option.FORCE,
                       option.USER_LEVEL:option.ADMIN,
                       },
        option.PARENT:{option.HELP_STRING:"prints the pnfs id of the parent " \
                       "directory (CWD must be under /pnfs)",
                       option.DEFAULT_VALUE:option.DEFAULT,
                       option.DEFAULT_NAME:"parent",
                       option.DEFAULT_TYPE:option.INTEGER,
                       option.VALUE_NAME:"pnfs_id",
                       option.VALUE_TYPE:option.STRING,
                       option.VALUE_USAGE:option.REQUIRED,
                       option.FORCE_SET_DEFAULT:option.FORCE,
                       option.USER_LEVEL:option.ADMIN,
                       },
        option.PATH:{option.HELP_STRING:"prints the file path of the pnfs id"\
                                        " (CWD must be under /pnfs)",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"path",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_NAME:"pnfs_id",
                     option.VALUE_TYPE:option.STRING,
                     option.VALUE_USAGE:option.REQUIRED,
                     option.FORCE_SET_DEFAULT:option.FORCE,
                     option.USER_LEVEL:option.ADMIN,
                     },
        option.POSITION:{option.HELP_STRING:"",
                         option.DEFAULT_VALUE:option.DEFAULT,
                         option.DEFAULT_NAME:"position",
                         option.DEFAULT_TYPE:option.INTEGER,
                         option.VALUE_NAME:"file",
                         option.VALUE_TYPE:option.STRING,
                         option.VALUE_USAGE:option.REQUIRED,
                         option.VALUE_LABEL:"filename",
                         option.FORCE_SET_DEFAULT:option.FORCE,
                         option.USER_LEVEL:option.ADMIN,
                         },
        option.RM:{option.HELP_STRING:"deletes (clears) named layer of the file",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"rm",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_NAME:"file",
                   option.VALUE_TYPE:option.STRING,
                   option.VALUE_USAGE:option.REQUIRED,
                   option.VALUE_LABEL:"filename",
                   option.FORCE_SET_DEFAULT:option.FORCE,
                   option.USER_LEVEL:option.ADMIN,
                   option.EXTRA_VALUES:[{option.VALUE_NAME:"named_layer",
                                         option.VALUE_TYPE:option.INTEGER,
                                         option.VALUE_USAGE:option.REQUIRED,
                                         option.VALUE_LABEL:"layer",
                                         },]
                   },
        option.SHOWID:{option.HELP_STRING:"prints the pnfs id information",
                       option.DEFAULT_VALUE:option.DEFAULT,
                       option.DEFAULT_NAME:"showid",
                       option.DEFAULT_TYPE:option.INTEGER,
                       option.VALUE_NAME:"pnfs_id",
                       option.VALUE_TYPE:option.STRING,
                       option.VALUE_USAGE:option.REQUIRED,
                       option.FORCE_SET_DEFAULT:option.FORCE,
                       option.USER_LEVEL:option.ADMIN,
                       },
        option.SIZE:{option.HELP_STRING:"sets the size of the file",
                     option.DEFAULT_VALUE:option.DEFAULT,
                     option.DEFAULT_NAME:"size",
                     option.DEFAULT_TYPE:option.INTEGER,
                     option.VALUE_NAME:"file",
                     option.VALUE_TYPE:option.STRING,
                     option.VALUE_USAGE:option.REQUIRED,
                     option.VALUE_LABEL:"filename",
                     option.FORCE_SET_DEFAULT:option.FORCE,
                     option.USER_LEVEL:option.USER2,
                     option.EXTRA_VALUES:[{option.VALUE_NAME:"filesize",
                                           option.VALUE_TYPE:option.LONG,
                                           option.VALUE_USAGE:option.REQUIRED,
                                           },]
                },
        option.TAGECHO:{option.HELP_STRING:"echos text to named tag",
                        option.DEFAULT_VALUE:option.DEFAULT,
                        option.DEFAULT_NAME:"tagecho",
                        option.DEFAULT_TYPE:option.INTEGER,
                        option.VALUE_NAME:"text",
                        option.VALUE_TYPE:option.STRING,
                        option.VALUE_USAGE:option.REQUIRED,
                        option.FORCE_SET_DEFAULT:option.FORCE,
                        option.USER_LEVEL:option.ADMIN,
                        option.EXTRA_VALUES:[{option.VALUE_NAME:"named_tag",
                                            option.VALUE_TYPE:option.STRING,
                                            option.VALUE_USAGE:option.REQUIRED,
                                            option.VALUE_LABEL:"tag",
                                              },]
                   },

        option.TAGRM:{option.HELP_STRING:"removes the tag (tricky, see DESY "
                                         "documentation)",
                      option.DEFAULT_VALUE:option.DEFAULT,
                      option.DEFAULT_NAME:"tagrm",
                      option.DEFAULT_TYPE:option.INTEGER,
                      option.VALUE_NAME:"named_tag",
                      option.VALUE_TYPE:option.STRING,
                      option.VALUE_USAGE:option.REQUIRED,
                      option.VALUE_LABEL:"tag",
                      option.FORCE_SET_DEFAULT:option.FORCE,
                      option.USER_LEVEL:option.ADMIN,
                 },
        option.UP:{option.HELP_STRING:"removes enstore system-down wormhole",
                   option.DEFAULT_VALUE:option.DEFAULT,
                   option.DEFAULT_NAME:"up",
                   option.DEFAULT_TYPE:option.INTEGER,
                   option.VALUE_USAGE:option.IGNORED,
                   option.USER_LEVEL:option.ADMIN,
                   },
        }
    
    def valid_dictionaries(self):
        return (self.help_options, self.pnfs_user_options,
                self.pnfs_admin_options)

    # parse the options like normal but make sure we have other args
    def parse_options(self):
        self.pnfs_id = "" #Assume the command is a dir and/or file.
        self.file = ""
        self.dir = ""
        option.Interface.parse_options(self)

        if not self.option_list:
            self.print_usage("No valid options were given.")

        #No pnfs options take extra arguments beyond those specifed in the
        # option dictionaries.  If there are print message and exit.
        self.check_correct_count()

        if getattr(self, "help", None):
            self.print_help()

        if getattr(self, "usage", None):
            self.print_usage()

##############################################################################

# This is a cleaner interface to access the tags in /pnfs

class Tag:
    def __init__(self, directory = None):
        self.dir = directory
    
    # write a new value to the specified tag
    # the file needs to exist before you call this
    # remember, tags are a propery of the directory, not of a file
    def writetag(self, tag, value, directory=None):
        if type(value) != types.StringType:
            value=str(value)
        if directory:
            fname = os.path.join(directory, ".(tag)(%s)"%(tag,))
        elif self.dir:
            fname = os.path.join(self.dir, ".(tag)(%s)"%(tag,))
        else:
            #Make sure that the current working directory is still valid.
            try:
                cwd = os.getcwd()
            except OSError, msg:
                #exc, msg = sys.exc_info()[:2]
                if msg.errno == errno.ENOENT:
                    msg_str = "%s: %s" % (os.strerror(errno.ENOENT),
                                          "No current working directory")
                    new_error = OSError(errno.ENOENT, msg_str)
                    raise OSError, new_error, sys.exc_info()[2]
                else:
                    raise sys.exc_info()
            fname = os.path.join(cwd, ".(tag)(%s)"%(tag,))

        #Make sure this is the full file path of the tag.
        fname = fullpath(fname)[1]

        #If directory is empty indicating the current directory, prepend it.
        #if not get_directory_name(self.dir):
        #    try:
        #        fname = os.path.join(os.getcwd(), fname)
        #    expect OSError:
        #        fname = ""

        #Determine if the target directory is in pnfs namespace
        if is_chimera_path(get_directory_name(fname)) == 0:
            raise IOError(errno.EINVAL,
                   os.strerror(errno.EINVAL) + ": Not a valid pnfs directory")

        try:
            f = file_utils.open(fname,'w')
            f.write(value)
            f.close()
        except (OSError, IOError):
            exc, msg = sys.exc_info()[:2]
            if msg.args[0] == errno.ENOTDIR:
                #If the error is ENOTDIR, then correct the path returned
                # to be the directory and not the tag file.
                use_msg = exc(errno.ENOTDIR, os.strerror(errno.ENOTDIR),
                              os.path.dirname(fname))
            else:
                use_msg = msg
            raise exc, use_msg, sys.exc_info()[2] #Don't have tb be local!

    # read the value stored in the requested tag
    def readtag(self, tag, directory=None):
        if directory:
            fname = os.path.join(directory, ".(tag)(%s)" % (tag,))
        elif self.dir:
            fname = os.path.join(self.dir, ".(tag)(%s)" % (tag,))
        else:
            #Make sure that the current working directory is still valid.
            try:
                cwd = os.getcwd()
            except OSError, msg:
                if msg.errno == errno.ENOENT:
                    msg_str = "%s: %s" % (os.strerror(errno.ENOENT),
                                          "No current working directory")
                    new_error = OSError(errno.ENOENT, msg_str)
                    raise OSError, new_error, sys.exc_info()[2]
                else:
                    raise sys.exc_info()
            fname = os.path.join(cwd, ".(tag)(%s)"%(tag,))

        #Make sure this is the full file path of the tag.
        fname = fullpath(fname)[1]
        
        #If directory is empty indicating the current directory, prepend it.
        #if not get_directory_name(self.dir):
        #    fname = os.path.join(os.getcwd(), fname)
        
        #Determine if the target directory is in pnfs namespace
        if is_chimera_path(get_directory_name(fname)) == 0:
            raise IOError(errno.EINVAL,
                   os.strerror(errno.EINVAL) + ": Not a valid chimera directory")

        try:
            f = file_utils.open(fname,'r')
            t = f.readlines()
            f.close()
        except (OSError, IOError):
            exc, msg = sys.exc_info()[:2]
            if msg.args[0] == errno.ENOTDIR:
                #If the error is ENOTDIR, then correct the path returned
                # to be the directory and not the tag file.
                use_msg = exc(errno.ENOTDIR, os.strerror(errno.ENOTDIR),
                              os.path.dirname(fname))
            else:
                use_msg = msg
            raise exc, use_msg, sys.exc_info()[2] #Don't have tb be local!
            
        return t

    ##########################################################################

    #Print out the current settings for all directory tags.
    def ptags(self, intf):

        #If the directory to use was passed in use that for the current
        # working directory.  Otherwise uses the current working directory.
        
        if hasattr(intf, "directory"):
            try:
                cwd = os.path.abspath(intf.directory)
            except OSError, detail:
                print detail
                return 1
        else:
            try:
                #Make sure that the current working directory is still valid.
                cwd = os.path.abspath(os.getcwd())
            except OSError:
                msg = sys.exc_info()[1]
                if msg.errno == errno.ENOENT:
                    msg_str = "%s: %s" % (os.strerror(errno.ENOENT),
                                          "No current working directory")
                    print msg_str
                else:
                    print msg
                return 1

        filename = os.path.join(cwd, ".(tags)(all)")

        try:
            f = file_utils.open(filename, "r")
            data = f.readlines()
            f.close()
        except IOError, detail:
            print detail
            return 1

        #print the top portion of the output.  Note: the values placed into
        # line have a newline at the end of them, this is why line[:-1] is
        # used to remove it.
        for line in data:
            try:
                tag = string.split(line[7:], ")")[0]
                tag_info = self.readtag(tag, directory = cwd)
                print line[:-1], "=",  tag_info[0]
            except (OSError, IOError, IndexError), detail:
                print line[:-1], ":", detail

        #Print the bottom portion of the output.
        for line in data:
            tag_file = os.path.join(cwd, line[:-1])
            os.system("ls -l \"" + tag_file + "\"")

        return 0
    
    def ptag(self, intf):
        try:
            if hasattr(intf, "directory") and intf.directory:
                tag = self.readtag(intf.named_tag, intf.directory)
            else:
                tag = self.readtag(intf.named_tag)
            print tag[0]
            return 0
        except (OSError, IOError, IndexError), detail:
            print str(detail)
            return 1

    def ptagecho(self, intf):
        try:
            self.writetag(intf.named_tag, intf.text)
        except (OSError, IOError), detail:
            print str(detail)
            return 1
        
    def ptagrm(self):  #, intf):
        print "Feature not yet implemented."

    ##########################################################################

    def ptagchown(self, intf):
        #Determine the directory to use.
        if self.dir:
            cwd = self.dir
        else:
            try:
                cwd = os.getcwd()
            except OSError, msg:
                if msg.errno == errno.ENOENT:
                    msg_str = "%s: %s" % (os.strerror(errno.ENOENT),
                                          "No current working directory")
                    print msg_str
                else:
                    print msg
                return 1

        #Format the tag filename string.
        fname = os.path.join(cwd, ".(tag)(%s)" % (intf.named_tag,))

        #Determine if the target directory is in pnfs namespace
        if fname[:6] != "/pnfs/":
            print os.strerror(errno.EINVAL) + ": Not a valid pnfs directory"
            return 1

        #Determine if the tag file exists.
        try:
            pstat = file_utils.get_stat(fname)
        except OSError, msg:
            print str(msg)
            return 1
        
        #Deterine the existing ownership.
        uid = pstat[stat.ST_UID]
        gid = pstat[stat.ST_GID]

        #Determine if the owner or owner.group was specified.
        owner = intf.owner.split(".")
        if len(owner) == 1:
            uid = owner[0]
        elif len(owner) == 2:
            uid = owner[0]
            gid = owner[1]
        else:
            print os.strerror(errno.EINVAL) + ": Incorrect owner field"
            return 1

        #If the user and group are ids, convert them to integers.
        try:
            uid = int(uid)
        except ValueError:
            pass
        try:
            gid = int(gid)
        except ValueError:
            pass
        
        if uid and type(uid) != types.IntType:
            try:
                uid = pwd.getpwnam(str(uid))[2]
            except KeyError:
                print os.strerror(errno.EINVAL) + ": Not a valid user"
                return 1

        if gid and type(gid) != types.IntType:
            try:
                gid = grp.getgrnam(str(gid))[2]
            except KeyError:
                print os.strerror(errno.EINVAL) + ": Not a valid group"
                return 1

        try:
            os.chown(fname, uid, gid)
            #os.utime(fname, None)
        except OSError, detail:
            print str(detail)
            return 1

        return 0


    def ptagchmod(self, intf):
        #Determine the directory to use.
        if self.dir:
            cwd = self.dir
        else:
            try:
                cwd = os.getcwd()
            except OSError, msg:
                if msg.errno == errno.ENOENT:
                    msg_str = "%s: %s" % (os.strerror(errno.ENOENT),
                                          "No current working directory")
                    print msg_str
                else:
                    print msg
                return 1
        
        #Format the tag filename string.
        fname = os.path.join(cwd, ".(tag)(%s)" % (intf.named_tag,))

        #Determine if the target directory is in pnfs namespace
        if fname[:6] != "/pnfs/":
            print os.strerror(errno.EINVAL) + ": Not a valid pnfs directory"
            return 1

        #Determine if the tag file exists.
        try:
            pstat = file_utils.get_stat(fname)
        except OSError, msg:
            print str(msg)
            return 1
        
        #Deterine the existing ownership.
        st_mode = pstat[stat.ST_MODE]

        try:
            #If the user entered the permission numerically, this is it...
            set_mode = enstore_functions2.numeric_to_bits(intf.permissions)
        except (TypeError, ValueError):
            #...else try the symbolic way.
            try:
                set_mode = enstore_functions2.symbolic_to_bits(
                    intf.permissions, st_mode)
            except (TypeError, ValueError):
                print "%s: Invalid permission field" % \
                      (os.strerror(errno.EINVAL),)
                return 1
        try:
            os.chmod(fname, int(set_mode))
            #os.utime(fname, None)
        except OSError, detail:
            print str(detail)
            return 1

        return 0
        
    ##########################################################################
    #Print or edit the library
    def plibrary(self, intf):
        try:
            if intf.library == 1:
                print self.get_library()
            else:
                if charset.is_string_in_character_set(intf.library,
                                                      charset.charset + ","):
                    #As of encp v3_6a allow the comma (,) character
                    # so that copies can be enabled.
                    self.set_library(intf.library)
                else:
                    print "Pnfs tag, library, contains invalid characters."
                    return 1
            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1

    #Print or edit the file family.
    def pfile_family(self, intf):
        try:
            if intf.file_family == 1:
                print self.get_file_family()
            else:
                #Restrict the characters allowed in the file_family.
                if not charset.is_in_charset(intf.file_family):
                    print "Pnfs tag, file_family, contains invalid characters."
                    return 1
                #Don't allow users to set file_families with the
                # migration pattern.
                elif re.search(".*-MIGRATION$", intf.file_family):
                    print "File familes ending in -MIGRATION are forbidden."
                    return 1
                #Don't allow users to set file_families with the
                # duplication pattern.
                elif re.search("_copy_[0-9]*$", intf.file_family):
                    print "File familes ending in _copy_# are forbidden."
                    return 1
                else:
                    self.set_file_family(intf.file_family)

            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1

    #Print or edit the file family wrapper.
    def pfile_family_wrapper(self, intf):
        try:
            if intf.file_family_wrapper == 1:
                print self.get_file_family_wrapper()
            else:
                if charset.is_in_charset(intf.file_family_wrapper):
                    self.set_file_family_wrapper(intf.file_family_wrapper)
                else:
                    print "Pnfs tag, file_family_wrapper, contains " \
                          "invalid characters."
                    return 1
            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1

    #Print or edit the file family width.
    def pfile_family_width(self, intf):
        try:
            if intf.file_family_width == 1:
                print self.get_file_family_width()
            else:
                if charset.is_in_charset(intf.file_family_width):
                    self.set_file_family_width(intf.file_family_width)
                else:
                    print "Pnfs tag, file_family_width, contains " \
                          "invalid characters."
                    return 1
            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1

    #Print or edit the storage group.
    def pstorage_group(self, intf):
        try:
            if intf.storage_group == 1:
                print self.get_storage_group()
            else:
                if charset.is_in_charset(intf.storage_group):
                    self.set_storage_group(intf.storage_group)
                else:
                    print "Pnfs tag, storage_group, contains " \
                          "invalid characters."
                    return 1
            return 0
        except (OSError, IOError), detail:
            print str(detail)
            return 1


    ##########################################################################

    # store a new tape library tag
    def set_library(self,value, directory=None):
        if directory:
            self.writetag("library", value, directory)
        else:
            self.writetag("library", value)
            self.get_library()
            
        return value

    # get the tape library
    def get_library(self, directory=None):
        try:
            if directory:
                library = self.readtag("library", directory)[0].strip()
            else:
                library = self.readtag("library")[0].strip()
                self.library = library
        except IndexError:
            #Only OSError and IOError should be raised.
            raise IOError(errno.EIO, "Library tag is empty.")
        
        return library
    
    ##########################################################################

    # store a new file family tag
    def set_file_family(self, value, directory=None):
        if directory:
            self.writetag("file_family", value, directory)
        else:
            self.writetag("file_family", value)
            self.get_file_family()

        return value

    # get the file family
    def get_file_family(self, directory=None):
        try:
            if directory:
                file_family = self.readtag("file_family", directory)[0].strip()
            else:
                file_family = self.readtag("file_family")[0].strip()
                self.file_family = file_family
        except IndexError:
            #Only OSError and IOError should be raised.
            raise IOError(errno.EIO, "File family tag is empty.")
        
        return file_family

    ##########################################################################

    # store a new file family wrapper tag
    def set_file_family_wrapper(self, value, directory=None):
        if directory:
            self.writetag("file_family_wrapper", value, directory)
        else:
            self.writetag("file_family_wrapper", value)
            self.get_file_family_wrapper()

        return value

    # get the file family
    def get_file_family_wrapper(self, directory=None):
        try:
            if directory:
                file_family_wrapper = self.readtag("file_family_wrapper",
                                                   directory)[0].strip()
            else:
                file_family_wrapper = self.readtag(
                    "file_family_wrapper")[0].strip()
                self.file_family_wrapper = file_family_wrapper
        except IndexError:
            #Only OSError and IOError should be raised.
            raise IOError(errno.EIO, "File family wrapper tag is empty.")
        
        return file_family_wrapper

    ##########################################################################

    # store a new file family width tag
    # this is the number of open files (ie simultaneous tapes) at one time
    def set_file_family_width(self, value, directory=None):
        if directory:
            self.writetag("file_family_width", value, directory)
        else:
            self.writetag("file_family_width", value)
            self.get_file_family_width()

        return value

    # get the file family width
    def get_file_family_width(self, directory=None):
        try:
            if directory:
                file_family_width = self.readtag("file_family_width",
                                                 directory)[0].strip()
            else:
                file_family_width = self.readtag(
                    "file_family_width")[0].strip()
                self.file_family_width = file_family_width
        except IndexError:
            #Only OSError and IOError should be raised.
            raise IOError(errno.EIO, "File family width tag is empty.")
        
        return file_family_width

    ##########################################################################

    # store a new storage group tag
    # this is group of volumes assigned to one experiment or group of users
    def set_storage_group(self, value, directory=None):
        if directory:
            self.writetag("storage_group", value, directory)
        else:
            self.writetag("storage_group", value)
            self.get_storage_group()

        return value

    # get the storage group
    def get_storage_group(self, directory=None):
        try:
            if directory:
                storage_group = self.readtag("storage_group",
                                             directory)[0].strip()
            else:
                storage_group = self.readtag("storage_group")[0].strip()
                self.storage_group = storage_group
        except IndexError:
            #Only OSError and IOError should be raised.
            raise IOError(errno.EIO, "Storage group tag is empty.")
        
        return storage_group

    ##########################################################################
            
    def penstore_state(self):  #, intf):
        fname = os.path.join(self.dir, ".(config)(flags)/disabled")
        print fname
        if os.access(fname, os.F_OK):# | os.R_OK):
            f=file_utils.open(fname,'r')
            self.enstore_state = f.readlines()
            f.close()
            print "Enstore disabled:", self.enstore_state[0],
        else:
            print "Enstore enabled"

    def ppnfs_state(self):  #, intf):
        fname = "%s/.(config)(flags)/.(id)(pnfs_state)" % self.dir
        if os.access(fname, os.F_OK | os.R_OK):
            f=file_utils.open(fname,'r')
            self.pnfs_state = f.readlines()
            f.close()
            print "Pnfs:", self.pnfs_state[0],
        else:
            print "Pnfs: unknown"

##############################################################################

class N:
    def __init__(self, dbnum, directory = None):
        if directory:
            self.dir = directory
        else:
            try:
                self.dir = os.getcwd()
            except OSError:
                self.dir = ""
        self.dbnum = dbnum
        self.databaseN = 0 

    # get the cursor information
    def get_countersN(self, dbnum=None):
        if dbnum != None:
            fname = os.path.join(self.dir,".(get)(counters)(%s)"%(dbnum,))
        else:
            fname = os.path.join(self.dir,".(get)(counters)(%s)"%(self.dbnum,))
        f=file_utils.open(fname,'r')
        self.countersN = f.readlines()
        f.close()
        return self.countersN

    # get the database information
    def get_databaseN(self, dbnum=None):
        return "admin:0:r:enabled:/srv2/pnfs/db/admin"

    def pdatabaseN(self, intf):
        try:
            self.get_databaseN(intf.dbnum)
            print_results(self.databaseN)
        except (OSError, IOError), detail:
            print str(detail)

    def pcountersN(self, intf):
        try:
            self.get_countersN(intf.dbnum)
            print_results(self.countersN)
        except (OSError, IOError), detail:
            print str(detail)

_mtab = None

# get_mtab() -- read /etc/mtab, for local/remote pnfs translation

def get_mtab():
    global _mtab
    if _mtab == None:
        _mtab = {}
        try:
            f = file_utils.open('/etc/mtab')
            l = f.readline()
            while l:
                lc = string.split(l)
                if lc[1][:5] == '/pnfs':
                    c1 = string.split(lc[0], ':')
                    if len(c1) > 1:
                        _mtab[lc[1]] = (c1[1], c1[0])
                    else:
                        _mtab[lc[1]] = (c1[0], None)
                l = f.readline()
            f.close()
        except:
            _mtab = {}
            f.close()
    return _mtab

LOCAL_PNFS_PREFIX = '/pnfs/fs/usr'

# get_local_pnfs_path(p) -- find local pnfs path

def get_local_pnfs_path(p):
    mtab = get_mtab()
    for i in mtab.keys():
        if string.find(p, i) == 0 and \
           string.split(os.uname()[1], '.')[0] == mtab[i][1]:
            p1 = os.path.join(LOCAL_PNFS_PREFIX, string.replace(p, i, mtab[i][0][1:]))
            if os.access(p1, os.F_OK):
                return p1
            else:
                return p
    return p

# get_abs_pnfs_path(p) -- find absolute pnfs path -- if /pnfs/fs is available

def get_abs_pnfs_path(p):
    mtab = get_mtab()
    for i in mtab.keys():
        if string.find(p, i) == 0:
            p1 = os.path.join(LOCAL_PNFS_PREFIX, string.replace(p, i, mtab[i][0][1:]))
            if os.access(p1, os.F_OK):
                return p1
            else:
                return p
    return p

# get_normal_pnfs_path(p)
#
# from /pnfs/fs/usr/XXX to get /pnfs/XXX

def get_normal_pnfs_path(p):
    # is it /pnfs/fs/usr*?
    if p[:12] != LOCAL_PNFS_PREFIX:
        return p

    p1= p[12:]
    mtab = get_mtab()
    for i in mtab.keys():
        if p1.find(mtab[i][0]) == 0:
            p2 = p1.replace(mtab[i][0], i)
            if os.access(p2, os.F_OK):
                return p2
            else:
                return p
    return p
    
# This is a cleaner interface to access the file, as well as its
# metadata, in /pnfs

class File:
	# the file could be a simple name, or a dictionary of file attributes
	def __init__(self, file):
		if type(file) == types.DictionaryType:  # a dictionary
			self.volume = file['external_label']
			self.location_cookie = file['location_cookie']
			self.size = str(file['size'])
			if file.has_key('file_family'):
				self.file_family = file['file_family']
			else:
				self.file_family = "unknown"
                        if file.has_key('pnfs_mapname'):
			    self.volmap = file['pnfs_mapname']
                        else:
                            self.volmap = ''
			self.pnfs_id = file['pnfsid']
                        if file.has_key('pnfsvid'):
			    self.pnfs_vid = file['pnfsvid']
                        else:
			    self.pnfs_vid = ''
			self.bfid = file['bfid']
			if file.has_key('drive'):
			    self.drive = file['drive']
			else:
			    self.drive = ''
			if file.has_key('pnfs_name0'):
			    self.path = file['pnfs_name0']
			else:
			    self.path = 'unknown'
			if file.has_key('complete_crc'):
			    self.complete_crc = str(file['complete_crc'])
			else:
			    self.complete_crc = ''
			self.p_path = self.path
		else:
			self.path = os.path.abspath(file)
			# does it exist?
                        try:
				f = file_utils.open(self.layer_file(4))
				finfo = map(string.strip, f.readlines())
				f.close()
				if len(finfo) == 11:
					self.volume,\
					self.location_cookie,\
					self.size, self.file_family,\
					self.p_path, self.volmap,\
					self.pnfs_id, self.pnfs_vid,\
					self.bfid, self.drive, \
					self.complete_crc = finfo
				elif len(finfo) == 10:
					self.volume,\
					self.location_cookie,\
					self.size, self.file_family,\
					self.p_path, self.volmap,\
					self.pnfs_id, self.pnfs_vid,\
					self.bfid, self.drive = finfo
					self.complete_crc = ''
				elif len(finfo) == 9:
					self.volume,\
					self.location_cookie,\
					self.size, self.file_family,\
					self.p_path, self.volmap,\
					self.pnfs_id, self.pnfs_vid,\
					self.bfid = finfo
					self.drive = "unknown:unknown"
					self.complete_crc = ''
				else:	# corrupted L4
					self.volume = "corrupted L4"
					self.location_cookie = ""
					self.size = None
					self.file_family = ""
					self.volmap = ""
					self.pnfs_id = "corrputed L4"
					self.pnfs_vid = ""
					self.bfid = "corrupted L4"
					self.drive = ""
					self.complete_crc = ''
					self.p_path = self.path
					
				# if self.p_path != self.path:
				#	raise 'DIFFERENT_PATH'
				#	print 'different paths'
				#	print '\t f>', self.path
				#	print '\t 4>', p_path
                        except IOError:
				self.volume = ""
				self.location_cookie = ""
				self.size = None
				self.file_family = ""
				self.volmap = ""
				self.pnfs_id = ""
				self.pnfs_vid = ""
				self.bfid = ""
				self.drive = ""
				self.complete_crc = ''
				self.p_path = self.path
			except:
				exc_type, exc_value = sys.exc_info()[:2]
				print exc_type, exc_value
		return

	# layer_file(i) -- compose the layer file name
	def layer_file(self, i):
		if self.file()[:9] == ".(access)":
			return "%s(%d)"%(self.path, i)
		else:
			return os.path.join(self.dir(),
                                    '.(use)(%d)(%s)'%(i, self.file()))

	# id_file() -- compose the id file name
	def id_file(self):
		return os.path.join(self.dir(), '.(id)(%s)'%(self.file()))

        # parent_file() -- compose the parent id file name
        def parent_file(self):
                try:
                        #Try and avoid unecessary .(id)() (P)NFS quires.
                        use_id = self.r_pnfs_id
                except AttributeError:
                        use_id = self.get_pnfs_id()

                return os.path.join(self.dir(), '.(parent)(%s)' % (use_id))

	# size_file -- compose the size file, except for the actual size
	def size_file(self):
		return os.path.join(self.dir(),
                                    '.(fset)(%s)(size)'%(self.file()))

	# dir() -- get the directory of this file
	def dir(self):
		return os.path.dirname(self.path)

	# file() -- get the basename of this file
	def file(self):
		return os.path.basename(self.path)

	# get_pnfs_id() -- get pnfs id from pnfs id file
	def get_pnfs_id(self):
		f = file_utils.open(self.id_file())
                self.r_pnfs_id = f.readline()[:-1]  #.strip()
		f.close()
		return self.r_pnfs_id

        # get_parent_id() -- get parent pnfs id from pnfs id file
        def get_parent_id(self):
                f = file_utils.open(self.parent_file())
                self.parent_id = f.readline()[:-1]  #.strip()
                f.close()
                return self.parent_id

	def show(self):
		print "           file =", self.path
		print "         volume =", self.volume
		print "location_cookie =", self.location_cookie
		print "           size =", self.size
		print "    file_family =", self.file_family
		print "         volmap =", self.volmap
		print "        pnfs_id =", self.pnfs_id
		print "       pnfs_vid =", self.pnfs_vid
		print "           bfid =", self.bfid
		print "          drive =", self.drive
		print "      meta-path =", self.p_path
		print "   complete_crc =", self.complete_crc
		return

	# set_size() -- set size in pnfs
	def set_size(self):
		if not self.exists():
			# do nothing if it doesn't exist
			return
		if long(self.size) > 2147483647L:
			size2 = 1
		else:
			size2 = long(self.size)
		real_size = file_utils.get_stat(self.path)[stat.ST_SIZE]
		if long(real_size) == long(size2):	# do nothing
			return
		size = str(size2)
		if size[-1] == 'L':
			size = size[:-1]
		fname = self.size_file()+'('+size+')'
		f = file_utils.open(fname, "w")
		f.close()
		real_size = file_utils.get_stat(self.path)[stat.ST_SIZE]
		if long(real_size) != long(size2):
			# oops, have to reset it again
			f = file_utils.open(fname, "w")
			f.close()
		return

	# update() -- write out to pnfs files
	def update(self, pnfsid=None):
		if not self.bfid:
			return
		if not self.consistent():
			raise ValueError('INCONSISTENT')
		if self.exists():
			# writing layer 1
			f = file_utils.open(self.layer_file(1), 'w')
			f.write(self.bfid)
			f.close()
			# writing layer 4
			f = file_utils.open(self.layer_file(4), 'w')
			f.write(self.volume+'\n')
			f.write(self.location_cookie+'\n')
			f.write(str(self.size)+'\n')
			f.write(self.file_family+'\n')
			f.write(self.p_path+'\n')
			f.write(self.volmap+'\n')
			if not pnfsid:
				# always use real pnfs id
				f.write(self.get_pnfs_id()+'\n')
			else:
				f.write(self.pnfs_id+'\n')
			f.write(self.pnfs_vid+'\n')
			f.write(self.bfid+'\n')
			f.write(self.drive+'\n')
			if self.complete_crc:
				f.write(str(self.complete_crc)+'\n')
			f.close()
			# set file size
			self.set_size()
		return

	# consistent() -- to see if data is consistent
	def consistent(self):
		# required field
		if not self.bfid or not self.volume \
                        or not self.size == None \
			or not self.location_cookie \
			or not self.file_family or not self.path \
			or not self.pnfs_id or not self.bfid \
			or not self.p_path:
			return 0
		return 1



	# exists() -- to see if the file exists in /pnfs area
	def exists(self):
		return os.access(self.path, os.F_OK)

	# create() -- create the file
	def create(self, pnfsid=None):
		# do not create if there is no BFID
		if not self.bfid:
			return
		if not self.exists() and self.consistent():
			f = file_utils.open(self.path, 'w')
			f.close()
			self.update(pnfsid)

	# update_bfid(bfid) -- change the bfid
	def update_bfid(self, bfid):
		if bfid != self.bfid:
			self.bfid = bfid
			self.update()

	# set() -- set values
	def set(self, file):
		changed = 0
		res = None
		if file.has_key('external_label'):
			self.volume = file['external_label']
			changed = 1
		if file.has_key('location_cookie'):
			self.location_cookie = file['location_cookie']
			changed = 1
		if file.has_key('size'):
			self.size = file['size']
			changed = 1
		if file.has_key('file_family'):
			self.file_family = file['file_family']
			changed = 1
		if file.has_key('pnfs_mapname'):
			self.volmap = file['pnfs_mapname']
			changed = 1
		if file.has_key('pnfsid'):
			self.pnfs_id = file['pnfsid']
			changed = 1
		if file.has_key('pnfsvid'):
			self.pnfs_vid = file['pnfsvid']
			changed = 1
		if file.has_key('bfid'):
			self.bfid = file['bfid']
			changed = 1
		if file.has_key('drive'):
			self.drive = file['drive']
			changed = 1
		if file.has_key('pnfs_name0'):
			self.path = file['pnfs_name0']
			changed = 1
		if file.has_key('complete_crc'):
			self.complete_crc = file['complete_crc']
			changed = 1
		if changed:
			res = self.update()
		return res


##############################################################################
def do_work(intf):

    rtn = 0

    try:
        if intf.file:
            p=Pnfs(intf.file)
            t=None
            n=None
        elif intf.pnfs_id:
            p=Pnfs(intf.pnfs_id, shortcut=True)
            t=None
            n=None
        elif hasattr(intf, "dbnum") and intf.dbnum:
            p=None
            t=None
            n=N(intf.dbnum)
        else:
            p=None
            t=Tag(intf.dir)
        n=None
    except OSError, msg:
        print str(msg)
        return 1
        
    for arg in intf.option_list:
        if string.replace(arg, "_", "-") in intf.options.keys():
            arg = string.replace(arg, "-", "_")
            for instance in [t, p, n]:
                if getattr(instance, "p"+arg, None):
                    try:
                        #Not all functions use/need intf passed in.
                        rtn = apply(getattr(instance, "p" + arg), ())
                    except TypeError:
                        rtn = apply(getattr(instance, "p" + arg), (intf,))
                    break
            else:
                print "p%s not found" % arg 
                rtn = 1

    return rtn

##############################################################################
if __name__ == "__main__":

    intf = PnfsInterface(user_mode=0)

    intf._mode = "admin"

    do_work(intf)