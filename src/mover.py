#!/usr/bin/env python

# $Id$

# python modules
import sys
import os
import threading
import errno
import pprint
import socket
import signal                           
import time                             
import string
import struct
import select
import exceptions
import traceback
import fcntl, FCNTL

# enstore modules

import setpath
import generic_server
import event_relay_client
import monitored_server
import enstore_constants
import interface
import dispatching_worker
import volume_clerk_client
import volume_family
import file_clerk_client                
import media_changer_client             
import callback
import checksum
import e_errors
import udp_client
import socket_ext
import hostaddr
import string_driver
import socket_ext

import Trace


"""
Mover:

  Any single mount or dismount failure ==> state=BROKEN, vol=NOACCESS
        (At some point, we'll want single failures to be 2-3 failures)

  Any single eject failure ==> state=BROKEN, vol=NOACCESS, no dismount
                                  attempt, tape stays in drive.

  Two consecutive transfer failures ==> state=BROKEN
        Exclude obvious failures like encp_gone.

  Any 3 transfer failures within an hour ==> state=BROKEN


  By BROKEN, I mean the normal ERROR state plus:
        a. Alarm is raised.
        b. Nanny doesn't fix it.   (enstore sched --down xxx   stops nanny)
        c. It's sticky across mover process restarts.
        d. Admin has to investigate and take drive out of BROKEN state.

I know I'm swinging way to far to the right and I know this is more
work for everyone, especially the admins.  Could you both please
comment and maybe suggest other things. I just don't want to come in
some morning and have all the D0 tapes torn apart and have the
possibility exist that Enstore could have done more to prevent it.


Jon
"""

"""TODO:

Make a class for EOD objects to handle all conversions from loc_cookie to integer
automatically.  It's confusing in this code when `eod' is a cookie vs a plain old int.


"""



class MoverError(exceptions.Exception):
    def __init__(self, arg):
        exceptions.Exception.__init__(self,arg)


#states
IDLE, SETUP, MOUNT_WAIT, SEEK, ACTIVE, HAVE_BOUND, DISMOUNT_WAIT, DRAINING, OFFLINE, CLEANING, ERROR = range(11)

_state_names=['IDLE', 'SETUP', 'MOUNT_WAIT', 'SEEK', 'ACTIVE', 'HAVE_BOUND', 'DISMOUNT_WAIT',
             'DRAINING', 'OFFLINE', 'CLEANING', 'ERROR']

##assert len(_state_names)==11

def state_name(state):
    return _state_names[state]

#modes
READ, WRITE = range(2)

#error sources
TAPE, ROBOT, NETWORK = ['TAPE', 'ROBOT', 'NETWORK']

def mode_name(mode):
    if mode is None:
        return None
    else:
        return ['READ','WRITE'][mode]

KB=1L<<10
MB=1L<<20
GB=1L<<30

SANITY_SIZE = 65536

class Buffer:
    def __init__(self, blocksize, min_bytes = 0, max_bytes = 1*MB):
        self.blocksize = blocksize
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes
        self.complete_crc = 0L
        self.sanity_crc = 0L
        self.sanity_bytes = 0L
        self.header_size = None
        self.trailer_size = 0L
        self.file_size = 0L
        self.bytes_written = 0L

        self.read_ok = threading.Event()
        self.write_ok = threading.Event()
        
        self._lock = threading.Lock()
        self._buf = []
        self._buf_bytes = 0L
        self._freelist = []
        self._reading_block = None
        self._writing_block = None
        self._read_ptr = 0
        self._write_ptr = 0
        self.wrapper = None
        self.first_block = 1
        self.bytes_for_crc = 0L
        
    def set_wrapper(self, wrapper):
        self.wrapper = wrapper

    def save_settings(self):
        self.saved_buf_bytes = self._buf_bytes
        self.saved_reading_block = self._reading_block
        self.saved_writing_block = self._writing_block
        self.saved_read_ptr = self._read_ptr
        self.saved_write_ptr = self._write_ptr
        self.saved_complete_crc = self.complete_crc
        self.saved_sanity_crc = self.sanity_crc
        self.saved_sanity_bytes = self.sanity_bytes
        self.saved_header_size = self.header_size
        self.saved_trailer_size = self.trailer_size
        self.saved_file_size = self.file_size
        self.saved_bytes_written = self.bytes_written
        self.saved_sanity_cookie = self.sanity_cookie
        self.saved_wrapper = self.wrapper

    def restore_settings(self):
        self._buf_bytes = self.saved_buf_bytes
        self._reading_block = self.saved_reading_block
        self._writing_block = self.saved_writing_block
        self._read_ptr = self.saved_read_ptr
        self._write_ptr = self.saved_write_ptr
        self.complete_crc = self.saved_complete_crc
        self.sanity_crc = self.saved_sanity_crc
        self.sanity_bytes = self.saved_sanity_bytes
        self.header_size = self.saved_header_size
        self.trailer_size = self.saved_trailer_size
        self.file_size = self.saved_file_size
        self.bytes_written = self.saved_bytes_written
        self.sanity_cookie = self.saved_sanity_cookie
        self.wrapper = self.saved_wrapper
        
    def reset(self, sanity_cookie, client_crc_on):
        self._lock.acquire()
        self.read_ok.set()
        self.write_ok.clear()
        
        self._buf = []
##        self._freelist = []   keep this around to save on malloc's
        self._buf_bytes = 0
        self._reading_block = None
        self._writing_block = None
        self._read_ptr = 0
        self._write_ptr = 0
        self.complete_crc = 0L
        self.sanity_crc = 0L
        self.sanity_bytes = 0L
        self.header_size = None
        self.trailer_size = 0L
        self.file_size = 0L
        self.bytes_written = 0L
        self._lock.release()
        self.sanity_cookie = sanity_cookie
        self.client_crc_on = client_crc_on
        self.wrapper = None
        self.first_block = 1
        
    def nbytes(self):
        return self._buf_bytes
        
    def full(self):
        return self.nbytes() >= self.max_bytes
    
    def empty(self):
        ## this means that a stream write would fail - we have no data at all to send
        self._lock.acquire()
        r =  len(self._buf) == 0 and not self._writing_block
        self._lock.release()
        return r
    
    def low(self):
        ## this means that either we don't have enough data for a full block,
        ## or we're deferring writes until enough data is buffered (min_bytes)
        self._lock.acquire()
        r = len(self._buf) == 0 or self.nbytes() < self.min_bytes
        self._lock.release()
        return r
    
    def set_min_bytes(self, min_bytes):
        self.min_bytes = min_bytes
        
    def set_blocksize(self, blocksize):
        if blocksize == self.blocksize:
            return
        if self.nbytes() != 0:
            raise "Buffer error: changing blocksize of nonempty buffer"
        self._lock.acquire()
        self._freelist = []
        self.blocksize = blocksize
        self._lock.release()
    
    def push(self, data):
        self._lock.acquire()
        self._buf.append(data)
        self._buf_bytes = self._buf_bytes + len(data)
        self._lock.release()
        
    def pull(self):
        self._lock.acquire()
        data = self._buf.pop(0)
        self._buf_bytes = self._buf_bytes - len(data)
        self._lock.release()
        return data
        
    def nonzero(self):
        return self.nbytes() > 0
    
    def __repr__(self):
        return "Buffer %s  %s  %s" % (self.min_bytes, self._buf_bytes, self.max_bytes)

    def block_read(self, nbytes, driver, fill_buffer=1):
        Trace.trace(22, "block_read CRC check is %s" % (self.client_crc_on,))
        if self.client_crc_on:
            # calculate checksum when reading from
            # tape (see comment in setup_transfer)
            do_crc = 1
        else:
            do_crc = 0
        data = None
        partial = None
        space = self._getspace()
        Trace.trace(22,"block_read: bytes_to_read: %s"%(nbytes,))
        bytes_read = driver.read(space, 0, nbytes)
        Trace.trace(22,"block_read: bytes_read: %s"%(bytes_read,))
        if bytes_read == nbytes: #normal case
            data = space
        elif bytes_read<=0: #error
            Trace.log(25, "block_read: read %s" % (bytes_read,))
            pass #XXX or raise an exception?
        else: #partial block read
            Trace.trace(25, "partial block (%s/%s) read" % (bytes_read,nbytes))
            data = space[:bytes_read]
            partial = 1

        data_ptr = 0
        bytes_for_crc = bytes_read
        bytes_for_cs = bytes_read

        if self.first_block: #Handle variable-sized cpio header
            ##if len(self.buffer._buf) != 1:
            ##        Trace.log(e_errors.ERROR,
            ##                  "block_read: error skipping over cpio header, len(buf)=%s"%(len(self.buffer._buf)))
            if len(data) >= self.wrapper.min_header_size:
                try:
                    header_size = self.wrapper.header_size(data)
                except (TypeError, ValueError), msg:
                    Trace.log(e_errors.ERROR,"Invalid header %s" %(data[:self.wrapper.min_header_size]))
                    raise "WRAPPER_ERROR"
                data_ptr = header_size
                bytes_for_cs = min(bytes_read - header_size, self.bytes_for_crc)
        if do_crc:
            crc_error = 0
            try:
                Trace.trace(22,"block_read: data_ptr %s, bytes_for_cs %s" % (data_ptr, bytes_for_cs))

                self.complete_crc = checksum.adler32_o(self.complete_crc,
                                                       data,
                                                       data_ptr, bytes_for_cs)
                Trace.trace(22,"block_read: complete_crc %s" % (self.complete_crc,))
                Trace.trace(22,"first_block %s, sanity_bytes %s"%(self.first_block,self.sanity_bytes))
                if self.first_block and self.sanity_bytes < SANITY_SIZE:
		    self.first_block = 0
                    nbytes = min(SANITY_SIZE-self.sanity_bytes, bytes_for_cs)
                    self.sanity_crc = checksum.adler32_o(self.sanity_crc,
                                                         data,
                                                         data_ptr, nbytes)
                    self.sanity_bytes = self.sanity_bytes + nbytes
                    Trace.trace(22, "block_read: sanity cookie %s sanity_crc %s sanity_bytes %s" %
                                (self.sanity_cookie, self.sanity_crc,
                                 self.sanity_bytes))
                    # compare sanity crc
                    if self.sanity_cookie and self.sanity_crc != self.sanity_cookie[1]:
                        Trace.log(e_errors.ERROR, "CRC Error: CRC sanity cookie %s, sanity CRC %s" %
                                  (self.sanity_cookie[1],self.sanity_crc)) 
                        crc_error = 1
                data_ptr = data_ptr + bytes_for_cs
            except:
                Trace.handle_error()
                raise "CRC_ERROR"
            if crc_error:
                raise "CRC_ERROR"
        else:
           self.first_block = 0 
                
##        Trace.trace(100, "block_read: len(buf)=%s"%(len(self._buf),)) #XXX remove CGW
        if data and fill_buffer:
            self.push(data)
            if partial:
                self._freespace(space)
                
        return bytes_read

    def block_write(self, nbytes, driver):
        Trace.trace(22,"block_write: bytes %s"%(nbytes,))
        
        if self.client_crc_on:
            # calculate checksum when reading from
            # tape (see comment in setup_transfer)
            do_crc = 1
        else:
            do_crc = 0
        Trace.trace(22,"block_write: header size %s"%(self.header_size,))
        data = self.pull() 
        if len(data)!=nbytes:
            raise ValueError, "asked to write %s bytes, buffer has %s" % (nbytes, len(data))
        bytes_written = driver.write(data, 0, nbytes)
        if bytes_written == nbytes: #normal case
            self.bytes_written = self.bytes_written + bytes_written
            Trace.trace(22, "block_write: bytes written %s" % (self.bytes_written))
            if do_crc:
                data_ptr = 0  # where data for CRC starts
                bytes_for_cs = bytes_written
                if self.first_block: #Handle variable-sized cpio header
                    #skip over the header
                    data_ptr = data_ptr + self.header_size
                    bytes_for_cs = bytes_for_cs - self.header_size
                    if len(data) <= self.header_size:
                        raise "WRAPPER_ERROR"
                if self.bytes_written == self.file_size:
                    # last block
                    bytes_for_cs = bytes_for_cs - self.trailer_size
                Trace.trace(22, "nbytes %s, bytes written %s, bytes for cs %s trailer size %s"%
                            (nbytes, bytes_written, bytes_for_cs,self.trailer_size))
                try:
                    Trace.trace(22,"block_write: data_ptr: %s, bytes_for_cs %s" %
                                (data_ptr, bytes_for_cs))
                    self.complete_crc = checksum.adler32_o(self.complete_crc,
                                                           data,
                                                           data_ptr, bytes_for_cs)
                    Trace.trace(22,"complete crc %s"%(self.complete_crc,))
                    
                    if self.first_block and self.sanity_bytes < SANITY_SIZE:
                        self.first_block = 0
                        nbytes = min(SANITY_SIZE-self.sanity_bytes, bytes_for_cs)
                        self.sanity_crc = checksum.adler32_o(self.sanity_crc,
                                                             data,
                                                             data_ptr, nbytes)
                        self.sanity_bytes = self.sanity_bytes + nbytes
                        Trace.trace(22, "block_write: sanity_crc %s sanity_bytes %s" %
                                    (self.sanity_crc, self.sanity_bytes))
                except:
                    raise "CRC_ERROR"
            self._freespace(data)
            
        else: #XXX raise an exception?
            Trace.trace(22,"actually written %s" % (bytes_written,))
            self._freespace(data)
        return bytes_written

        
    def stream_read(self, nbytes, driver):
        if not self.client_crc_on:
            # calculate checksum when reading from
            # the network (see comment in setup_transfer)
            # CRC when receiving from the network if client does not CRC
            do_crc = 1
        else:
            do_crc = 0
            
        if type(driver) is type (""):
            driver = string_driver.StringDriver(driver)
        if isinstance(driver, string_driver.StringDriver):
            do_crc = 0
        if not self._reading_block:
            self._reading_block = self._getspace()
            self._read_ptr = 0
        bytes_to_read = min(self.blocksize - self._read_ptr, nbytes)
        bytes_read = driver.read(self._reading_block, self._read_ptr, bytes_to_read)
        if do_crc:
            Trace.trace(22,"nbytes %s, bytes_to_read %s, bytes_read %s" %
                        (nbytes, bytes_to_read, bytes_read))
            self.complete_crc = checksum.adler32_o(self.complete_crc, self._reading_block,
                                                   self._read_ptr, bytes_read)
            if self.sanity_bytes < SANITY_SIZE:
                nbytes = min(SANITY_SIZE-self.sanity_bytes, bytes_read)
                self.sanity_crc = checksum.adler32_o(self.sanity_crc, self._reading_block,
                                                     self._read_ptr, nbytes)
                self.sanity_bytes = self.sanity_bytes + nbytes
        self._read_ptr = self._read_ptr + bytes_read
        if self._read_ptr == self.blocksize: #we filled up  a block
            self.push(self._reading_block)
            self._reading_block = None
            self._read_ptr = 0
        return bytes_read

    def eof_read(self):
        Trace.trace(10, "EOF reached, %s"%(self._read_ptr,))
        if self._reading_block and self._read_ptr:
            data = self._reading_block[:self._read_ptr]
            self.push(data)
            self._reading_block = None
            self._read_ptr = None
    
    def stream_write(self, nbytes, driver):
        if not self.client_crc_on:
            # calculate checksum when writing to
            # the network (see comment in setup_transfer)
            # CRC when sending to the network if client does not CRC
            do_crc = 1
        else:
            do_crc = 0
        if not self._writing_block:
            if self.empty():
                Trace.trace(10, "stream_write: buffer empty")
                return 0
            self._writing_block = self.pull()
            self._write_ptr = 0
        bytes_to_write = min(len(self._writing_block)-self._write_ptr, nbytes)
        if driver:
            bytes_written = driver.write(self._writing_block, self._write_ptr, bytes_to_write)
            if do_crc:
                self.complete_crc = checksum.adler32_o(self.complete_crc,
                                                       self._writing_block,
                                                       self._write_ptr, bytes_written)
                if self.sanity_bytes < SANITY_SIZE:
                    nbytes = min(SANITY_SIZE-self.sanity_bytes, bytes_written)
                    self.sanity_crc = checksum.adler32_o(self.sanity_crc,
                                                         self._writing_block,
                                                         self._write_ptr, nbytes)
                    self.sanity_bytes = self.sanity_bytes + nbytes

                    Trace.trace(22, "stream_write: sanity cookie %s sanity_crc %s sanity_bytes %s" %
                                (self.sanity_cookie, self.sanity_crc,self.sanity_bytes))
                    # compare sanity crc
                    if self.sanity_cookie and self.sanity_crc != self.sanity_cookie[1]:
                        Trace.log(e_errors.ERROR, "CRC Error: CRC sanity cookie %s, sanity CRC %s" % (self.sanity_cookie[1],self.sanity_crc)) 
                        raise "CRC_ERROR"
        else:
            bytes_written = bytes_to_write #discarding header stuff
        self._write_ptr = self._write_ptr + bytes_written
        if self._write_ptr == len(self._writing_block): #finished sending out this block
            self._freespace(self._writing_block)
            self._writing_block = None
            self._write_ptr = 0
        return bytes_written

    def _getspace(self):
        self._lock.acquire()
        if self._freelist:
            r =  self._freelist.pop(0)
        else:
            r = struct.pack("%ss" % (self.blocksize,), '')
        self._lock.release()
        return r
    
    def _freespace(self, s):
        if len(s) != self.blocksize:
            return # don't need this partial block around!
        self._lock.acquire()
        self._freelist.append(s)
        self._lock.release()
    
def cookie_to_long(cookie): # cookie is such a silly term, but I guess we're stuck with it :-(
    if type(cookie) is type(0L):
        return cookie
    if type(cookie) is type(0):
        return long(cookie)
    if type(cookie) != type(''):
        raise TypeError, "expected string or integer, got %s %s" % (cookie, type(cookie))
    if '_' in cookie:
        part, block, file = string.split(cookie, '_')
    else:
        file = cookie
    if file[-1]=='L':
        file = file[:-1]
    return long(file)

def loc_to_cookie(loc):
    if type(loc) is type(""):
        loc = cookie_to_long(loc)
    if loc is None:
        loc = 0
    return '%04d_%09d_%07d' % (0, 0, loc)

_host_type = None

Linux, IRIX, Solaris, Other = range(4)

def host_type():
    global _host_type
    if _host_type:
        return _host_type
    uname = string.upper(os.uname()[0])
    _host_type = {'linux':Linux, 'irix':IRIX, 'sunos': Solaris}.get(uname, Other)
    return _host_type
    
class Mover(dispatching_worker.DispatchingWorker,
            generic_server.GenericServer):

    def __init__(self, csc_address, name):
        generic_server.GenericServer.__init__(self, csc_address, name)
        self.name = name
        self.shortname = name
        self.unique_id = None #Unique id of last transfer, whether success or failure
        self.notify_transfer_threshold = 2*1024*1024
        self.state_change_time = 0.0
        self._state_lock = threading.Lock()
        if self.shortname[-6:]=='.mover':
            self.shortname = name[:-6]
        self.draining = 0
        
        # self.need_lm_update is used in threads to flag LM update in
        # the main thread. First element flags update if not 0,
        # second - state
        # third -  reset timer
        # fourth - error source
        self.need_lm_update = (0, None, 0, None) 
        
    def __setattr__(self, attr, val):
        #tricky code to catch state changes
        try:
            if attr == 'state':
                if val != getattr(self, 'state', None):
                    Trace.notify("state %s %s" % (self.shortname, state_name(val)))
                self.__dict__['state_change_time'] = time.time()
        except:
            pass #don't want any errors here to stop us
        self.__dict__[attr] = val

    def return_state(self):
        return state_name(self.state)

    def lock_state(self):
        self._state_lock.acquire()

    def unlock_state(self):
        self._state_lock.release()


    ## XXX These functions work by way of rsh, because there
    ## is not a proper client/server interface to the 'enstore sched'
    ## commands - they must be run on the node where the html
    ## pages are stored - sigh - there should really be a sched_client
    def check_sched_down(self):
        inq = self.csc.get('inquisitor')
        host = inq.get('host')
        if not host:
            return 0
        cmd = 'enrsh -n %s \' su -c ". /usr/local/etc/setups.sh; setup enstore; enstore sched --show" enstore\'' % (host,)
        p = os.popen(cmd, 'r')
        r = p.read()
        s = p.close()
        if s:
            Trace.log(e_errors.ERROR, "error running enstore sched: %s" % (s,))
            return 0
        lines = string.split(r,'\n')
        roi = 0
        for line in lines:
            words = string.split(line)
            if 'Enstore' in words:
                roi = 'Known' in words
                continue
            if roi and self.name in words:
                return 1
        return 0
    
    ## XXX These functions work by way of rsh, because there
    ## is not a proper client/server interface to the 'enstore sched'
    ## commands - they must be run on the node where the html
    ## pages are stored - sigh - there should really be a sched_client
    def set_sched_down(self):
        inq = self.csc.get('inquisitor')
        host = inq.get('host')
        if not host:
            return 0
        cmd = 'enrsh -n %s \' su -c ". /usr/local/etc/setups.sh; setup enstore; enstore sched --down=%s; enstore system" enstore\'' % (
            host, self.name)
        p = os.popen(cmd, 'r')
        r = p.read()
        s = p.close()
        if s:
            Trace.log(e_errors.ERROR, "error running enstore sched: %s" % (s,))
        
    def start(self):
        name = self.name
        self.t0 = time.time()
        self.config = self.csc.get(name)
        if self.config['status'][0] != 'ok':
            raise MoverError('could not start mover %s: %s'%(name, self.config['status']))

        logname = self.config.get('logname', name)
        Trace.init(logname)
        Trace.log(e_errors.INFO, "starting mover %s" % (self.name,))
        
        #how often to send an alive heartbeat to the event relay
        self.alive_interval = monitored_server.get_alive_interval(self.csc, name, self.config)
        self.address = (self.config['hostip'], self.config['port'])

        self.do_eject = 1
        if self.config.has_key('do_eject'):
            if self.config['do_eject'][0] in ('n','N'):
                self.do_eject = 0

        self.do_cleaning = 1
        if self.config.has_key('do_cleaning'):
            if self.config['do_cleaning'][0] in ('n','N'):
                self.do_cleaning = 0
        
        self.mc_device = self.config.get('mc_device', 'UNDEFINED')
        self.media_type = self.config.get('media_type', '8MM') #XXX
        self.min_buffer = self.config.get('min_buffer', 8*MB)
        self.max_buffer = self.config.get('max_buffer', 64*MB)
        self.max_rate = self.config.get('max_rate', 11.2*MB) #XXX
        self.check_written_file_period = self.config.get('check_written_file', 0)
        self.files_written_cnt = 0
        self.buffer = Buffer(0, self.min_buffer, self.max_buffer)
        self.udpc = udp_client.UDPClient()
        self.state = IDLE
        self.last_error = (e_errors.OK, None)
        if self.check_sched_down() or self.check_lockfile():
            self.state = OFFLINE
        self.current_location = 0L
        self.current_volume = None #external label of current mounted volume
        self.last_location = 0L
        self.last_volume = None
        self.mode = None # READ or WRITE
        self.bytes_to_transfer = 0L
        self.bytes_to_read = 0L
        self.bytes_to_write = 0L
        self.bytes_read = 0L
        self.bytes_written = 0L
        self.volume_family = None 
        self.files = ('','')
        self.transfers_completed = 0
        self.transfers_failed = 0
        self.error_times = []
        self.consecutive_failures = 0
        self.max_consecutive_failures = 2
        self.max_failures = 3
        self.failure_interval = 3600
        self.current_work_ticket = {}
        self.vol_info = {}
        self.file_info = {}
        self.dismount_time = None
        self.delay = 0
        self.fcc = None
        self.vcc = None
        self.mcc = media_changer_client.MediaChangerClient(self.csc,
                                                           self.config['media_changer'])
        mc_keys = self.csc.get(self.mcc.media_changer)
        # STK robot can eject tape by either sending command directly to drive or
        # by pushing a corresponding button
        if mc_keys.has_key('type') and mc_keys['type'] is 'STK_MediaLoader':
            self.can_force_eject = 1
        else:
            self.can_force_eject = 0
        
        self.config['device'] = os.path.expandvars(self.config['device'])
        self.client_hostname = None
        self.client_ip = None  #NB: a client may have multiple interfaces, this is
                                         ##the IP of the interface we're using
        
        import net_driver
        self.net_driver = net_driver.NetDriver()
        self.client_socket = None

        self.config['name']=self.name 
        self.config['product_id']='Unknown'
        self.config['serial_num']=0
        self.config['vendor_id']='Unknown'
        self.config['local_mover'] = 0 #XXX who still looks at this?
        self.driver_type = self.config['driver']

        self.max_consecutive_failures = self.config.get('max_consecutive_failures',
                                                        self.max_consecutive_failures)
        self.max_failures = self.config.get("max_failures", self.max_failures)
        self.failure_interval = self.config.get("failure_interval", self.failure_interval)
        
        self.default_dismount_delay = self.config.get('dismount_delay', 60)
        if self.default_dismount_delay < 0:
            self.default_dismount_delay = 31536000 #1 year
        self.max_dismount_delay = max(
            self.config.get('max_dismount_delay', 600),
            self.default_dismount_delay)
        
        self.libraries = []
        lib_list = self.config['library']
        if type(lib_list) != type([]):
            lib_list = [lib_list]
        for lib in lib_list:
            lib_config = self.csc.get(lib)
            self.libraries.append((lib, (lib_config['hostip'], lib_config['port'])))

        #how often to send a message to the library manager
        self.update_interval = self.config.get('update_interval', 15)

        self.single_filemark=self.config.get('single_filemark', 0)
        ##Setting this attempts to optimize filemark writing by writing only
        ## a single filemark after each file, instead of using ftt's policy of always
        ## writing two and backspacing over one.  However this results in only
        ## a single filemark at the end of the volume;  causing some drives
        ## (e.g. Mammoth-1) to have trouble spacing to end-of-media.
            
        if self.driver_type == 'NullDriver':
            self.device = None
            self.single_filemark = 1 #need this to cause EOD cookies to update.
            ##XXX should this be more encapsulated in the driver class?
            import null_driver
            self.tape_driver = null_driver.NullDriver()
        elif self.driver_type == 'FTTDriver':
            self.device = self.config['device']
            import ftt_driver
            import ftt
            self.tape_driver = ftt_driver.FTTDriver()
            have_tape = 0
            if self.state is IDLE:
                have_tape = self.tape_driver.open(self.device, mode=0, retry_count=3)

                stats = self.tape_driver.ftt.get_stats()
                self.config['product_id'] = stats[ftt.PRODUCT_ID]
                self.config['serial_num'] = stats[ftt.SERIAL_NUM]
                self.config['vendor_id'] = stats[ftt.VENDOR_ID]

                if have_tape == 1:
                    status = self.tape_driver.verify_label(None)
                    if status[0]==e_errors.OK:
                        self.current_volume = status[1]
                        self.state = HAVE_BOUND
                        Trace.log(e_errors.INFO, "have vol %s at startup" % (self.current_volume,))
                        self.dismount_time = time.time() + self.default_dismount_delay
                    else:
                        have_tape=0
                self.tape_driver.close()
                if not have_tape:
                    Trace.log(e_errors.INFO, "performing precautionary dismount at startup")
                    vol_ticket = { "external_label": "Unknown",
                                   "media_type":self.media_type}
                    mcc_reply = self.mcc.unloadvol(vol_ticket, self.name, self.mc_device)

                if self.maybe_clean():
                    have_tape = 0
                    
        else:
            print "Sorry, only Null and FTT driver allowed at this time"
            sys.exit(-1)

        self.mount_delay = self.config.get('mount_delay',
                                           self.tape_driver.mount_delay)
        
        if type(self.mount_delay) != type(0):
            self.mount_delay = int(self.mount_delay)
        if self.mount_delay < 0:
            self.mount_delay = 0

        dispatching_worker.DispatchingWorker.__init__(self, self.address)
        self.add_interval_func(self.update_lm, self.update_interval) #this sets the period for messages to LM.
        self.add_interval_func(self.need_update, 1) #this sets the period for checking if child thread has asked for update.
        self.set_error_handler(self.handle_mover_error)
        ##start our heartbeat to the event relay process
        self.erc.start_heartbeat(self.name, self.alive_interval, self.return_state)
        ##end of __init__

    def check_written_file(self):
        if self.check_written_file_period and self.files_written_cnt%self.check_written_file_period == 0:
            return 1
        else:
            return 0
        
    def nowork(self, ticket):
        return {}

    def handle_mover_error(self, exc, msg, tb):
        Trace.log(e_errors.ERROR, "handle mover error %s %s"%(exc, msg))
        Trace.trace(10, "%s %s" %(self.current_work_ticket, state_name(self.state)))
        if self.current_work_ticket:
            try:
                Trace.trace(10, "handle error: calling transfer failed, str(msg)=%s"%(str(msg),))
                self.transfer_failed(exc, msg)
            except:
                pass

    ## This is the function which is responsible for updating the LM.
    def update_lm(self, state=None, reset_timer=None, error_source=None):
        self.need_lm_update = (0, None, 0, None)
        if state is None:
            state = self.state
        
        Trace.trace(20,"update_lm: %s %s" % (state_name(state), self.unique_id))
        inhibit = 0
        thread = threading.currentThread()
        if thread:
            thread_name = thread.getName()
        else:
            thread_name = None
        Trace.trace(20, "update_lm: thread %s"% (thread_name,))

        if not hasattr(self,'_last_state'):
            self._last_state = None

        now = time.time()
        if self.state is HAVE_BOUND and self.dismount_time and self.dismount_time-now < 5:
            #Don't tease the library manager!
            inhibit = 1

        if reset_timer:
            self.reset_interval_timer(self.update_lm)

        if not inhibit:
            # only main thread is allowed to send messages to LM
            ticket = self.format_lm_ticket(state=state, error_source=error_source)
            for lib, addr in self.libraries:
                if state != self._last_state:
                    Trace.trace(10, "update_lm: %s to %s" % (ticket, addr))
                self._last_state = self.state
                # only main thread is allowed to send messages to LM
                # exception is a mover_busy and mover_error works
                if ((thread_name is 'MainThread') and
                    (ticket['work'] is not 'mover_busy')
                    and (ticket['work'] is not 'mover_error')):
                    Trace.trace(20,"update_lm: send with wait %s"%(ticket['work'],))
                    ## XXX Sasha - this is an experiment - not sure this is a good idea!
                    try:
                        request_from_lm = self.udpc.send(ticket, addr)
                    except:
                        exc, msg, tb = sys.exc_info()
                        if exc == errno.errorcode[errno.ETIMEDOUT]:
                            x = {'status' : (e_errors.TIMEDOUT, msg)}
                        else:
                            x = {'status' : (str(exc), str(msg))}
                        Trace.trace(10, "update_lm: got %s" %(x,))
                        continue
                    work = request_from_lm.get('work')
                    if not work or work=='nowork':
                        continue
                    method = getattr(self, work, None)
                    if method:
                        method(request_from_lm)
                        ### XXX Try/except here?
                # if work is mover_busy of mover_error
                # send no_wait message
                if (ticket['work'] is 'mover_busy') or (ticket['work'] is 'mover_error'):
                    Trace.trace(20,"update_lm: send with no wait %s"%(ticket['work'],))
                    self.udpc.send_no_wait(ticket, addr)
                        
        self.check_dismount_timer()


    def need_update(self):
        if self.need_lm_update[0]:
            Trace.trace(20," need_update calling update_lm") 
            self.update_lm(state = self.need_lm_update[1],
                           reset_timer=self.need_lm_update[2],
                           error_source=self.need_lm_update[3])
            
    def _do_delayed_update_lm(self):
        for x in xrange(3):
            time.sleep(1)
            self.update_lm()
        
    def delayed_update_lm(self):
        self.run_in_thread('delayed_update_thread', self._do_delayed_update_lm)
        
    def check_dismount_timer(self):
        self.lock_state()
        ## See if the delayed dismount timer has expired
        now = time.time()
        if self.state is HAVE_BOUND and self.dismount_time and now>self.dismount_time:
            self.state = DISMOUNT_WAIT
            self.unlock_state()
            Trace.trace(10,"Dismount time expired %s"% (self.current_volume,))
            self.run_in_thread('media_thread', self.dismount_volume, after_function=self.idle)
        else:
            self.unlock_state()
            
    def idle(self):
        if self.state == ERROR:
            return
        if not self.do_eject:
            return
        self.state = IDLE
        self.mode = None
        self.vol_info = {}
        self.file_info = {}
        thread = threading.currentThread()
        if thread:
            thread_name = thread.getName()
        else:
            thread_name = None
        # if running in the main thread update lm
        if thread_name is 'MainThread':
            self.update_lm() 
        else: # else just set the update flag
            self.need_lm_update = (1, None, 0, None)

    def offline(self):
        self.state = OFFLINE
        self.update_lm()

    def reset(self, sanity_cookie, client_crc_on):
        self.current_work_ticket = None
        self.buffer.reset(sanity_cookie, client_crc_on)
        self.bytes_read = 0L
        self.bytes_written = 0L

    def return_work_to_lm(self,ticket):
        Trace.trace(21, "return_work_to_lm %s"%(ticket,))
        try:
            lm_address = ticket['lm']['address']
        except KeyError, msg:
            Trace.trace(21, "return_work_to_lm failed %"%(msg,))
            self.malformed_ticket(ticket, "[lm][address]")
            return

        ticket = self.format_lm_ticket(state=ERROR,
                                       error_info=(e_errors.MOVER_BUSY, state_name(self.state)),
                                       returned_work=ticket)
        self.udpc.send_no_wait(ticket, lm_address)

        
    def read_client(self):
        Trace.trace(8, "read_client starting,  bytes_to_read=%s" % (self.bytes_to_read,))
        driver = self.net_driver
        if self.bytes_read == 0 and self.header: #splice in cpio headers, as if they came from client
            nbytes = self.buffer.header_size
            ##XXX this will fail if nbytes>block_size.
            bytes_read = self.buffer.stream_read(nbytes,self.header)
        bytes_notified = 0L
        threshold = self.notify_transfer_threshold
        if threshold * 5 > self.bytes_to_read:
            threshold = self.bytes_to_read/5
        elif threshold * 100 < self.bytes_to_read:
            threshold = self.bytes_to_read/100
            
        while self.state in (ACTIVE, DRAINING) and self.bytes_read < self.bytes_to_read:
            if self.buffer.full():
                Trace.trace(9, "read_client: buffer full %s/%s, read %s/%s" %
                            (self.buffer.nbytes(), self.buffer.max_bytes,
                             self.bytes_read, self.bytes_to_read))
                self.buffer.read_ok.clear()
                self.buffer.read_ok.wait(1)
                continue

            nbytes = min(self.bytes_to_read - self.bytes_read, self.buffer.blocksize)
            bytes_read = 0
            try:
                bytes_read = self.buffer.stream_read(nbytes, driver)
            except:
                exc, detail, tb = sys.exc_info()
                Trace.handle_error(exc, detail, tb)
                self.transfer_failed(e_errors.ENCP_GONE, detail)
                return
            if bytes_read <= 0:  #  The client went away!
                Trace.log(e_errors.ERROR, "read_client: dropped connection")
                if self.state is not DRAINING: self.state = HAVE_BOUND
                # if state is DRAINING transfer_failed will set it to OFFLINE
                self.transfer_failed(e_errors.ENCP_GONE, None)
                return
            self.bytes_read = self.bytes_read + bytes_read

            if not self.buffer.low():
                self.buffer.write_ok.set()

            if bytes_notified==0 or self.bytes_read - bytes_notified > threshold:
                bytes_notified = self.bytes_read
                Trace.notify("transfer %s %s %s network" % (self.shortname, self.bytes_read, self.bytes_to_read))
                
        if self.bytes_read == self.bytes_to_read:
            if self.trailer:
                trailer_driver = string_driver.StringDriver(self.trailer)
                trailer_bytes_read = 0
                while trailer_bytes_read < self.buffer.trailer_size:
                    bytes_to_read = self.buffer.trailer_size - trailer_bytes_read
                    bytes_read = self.buffer.stream_read(bytes_to_read, trailer_driver)
                    trailer_bytes_read = trailer_bytes_read + bytes_read
                    Trace.trace(8, "read %s bytes of trailer" % (trailer_bytes_read,))
            self.buffer.eof_read() #pushes last partial block onto the fifo
            self.buffer.write_ok.set()

        Trace.trace(8, "read_client exiting, read %s/%s bytes" %(self.bytes_read, self.bytes_to_read))
                        
    def write_tape(self):
        Trace.trace(8, "write_tape starting, bytes_to_write=%s" % (self.bytes_to_write,))
        Trace.trace(8, "bytes_to_transfer=%s" % (self.bytes_to_transfer,))
        driver = self.tape_driver
        count = 0
        defer_write = 1
        failed = 0
        while self.state in (ACTIVE, DRAINING) and self.bytes_written<self.bytes_to_write:
            empty = self.buffer.empty()
            if (empty or
                (defer_write and (self.bytes_read < self.bytes_to_read and self.buffer.low()))):
                if empty:
                    defer_write = 1
                Trace.trace(9,"write_tape: buffer low %s/%s, wrote %s/%s, defer=%s"%
                            (self.buffer.nbytes(), self.buffer.min_bytes,
                             self.bytes_written, self.bytes_to_write,
                             defer_write))
                self.buffer.write_ok.clear()
                self.buffer.write_ok.wait(1)
                if (defer_write and (self.bytes_read==self.bytes_to_read or not self.buffer.low())):
                    defer_write = 0
                continue

            count = (count + 1) % 20
            if count == 0:
                ##Dynamic setting of low-water mark
                if self.bytes_read >= self.buffer.min_bytes:
                    netrate, junk = self.net_driver.rates()
                    taperate = self.max_rate
                    if taperate > 0:
                        ratio = netrate/(taperate*1.0)
                        optimal_buf = self.bytes_to_transfer * (1-ratio)
                        optimal_buf = min(optimal_buf, 0.5 * self.max_buffer)
                        optimal_buf = max(optimal_buf, self.min_buffer)
                        optimal_buf = int(optimal_buf)
                        Trace.trace(12,"netrate = %.3g, taperate=%.3g" % (netrate, taperate))
                        if self.buffer.min_bytes != optimal_buf:
                            Trace.trace(12,"Changing buffer size from %s to %s"%
                                        (self.buffer.min_bytes, optimal_buf))
                            self.buffer.set_min_bytes(optimal_buf)

            nbytes = min(self.bytes_to_write - self.bytes_written, self.buffer.blocksize)

            bytes_written = 0
            try:
                bytes_written = self.buffer.block_write(nbytes, driver)
            except:
                exc, detail, tb = sys.exc_info()
                Trace.handle_error(exc, detail, tb)
                self.transfer_failed(e_errors.WRITE_ERROR, detail, error_source=TAPE)
                failed = 1
                break
            if bytes_written != nbytes:
                self.transfer_failed(e_errors.WRITE_ERROR, "short write %s != %s" %
                                     (bytes_written, nbytes), error_source=TAPE)
                failed = 1
                break
            self.bytes_written = self.bytes_written + bytes_written

            if not self.buffer.full():
                self.buffer.read_ok.set()
        Trace.notify("transfer %s %s %s media" % (self.shortname, self.bytes_written, self.bytes_to_write))
        Trace.trace(8, "write_tape exiting, wrote %s/%s bytes" %( self.bytes_written, self.bytes_to_write))

        if failed: return
        if self.bytes_written == self.bytes_to_write:
            try:
                if self.single_filemark:
                    self.tape_driver.writefm()
                else:
                    self.tape_driver.writefm()
                    self.tape_driver.writefm()
                    self.tape_driver.skipfm(-1)
                ##We don't ever want to let ftt handle the filemarks for us, because its
                ##default behavior is to write 2 filemarks and backspace over both
                ##of them.
                self.tape_driver.flush()
            except:
                exc, detail, tb = sys.exc_info()
                self.transfer_failed(e_errors.WRITE_ERROR, detail, error_source=TAPE)
                return
            
            if self.check_written_file() and self.driver_type == 'FTTDriver':
                Trace.log(e_errors.INFO, "selective CRC check after writing file")
                Trace.trace(22, "position media")
                have_tape = self.tape_driver.open(self.device, self.mode, retry_count=30)
                self.tape_driver.set_mode(blocksize = 0)
                save_location = self.tape_driver.tell()
                Trace.trace(22,"save location %s" % (save_location,))
                if have_tape != 1:
                    self.transfer_failed(e_errors.WRITE_ERROR, "error positionong tape for selective CRC check", error_source=TAPE)
                    return
                try:
                    self.tape_driver.seek(cookie_to_long(self.vol_info['eod_cookie']), 0) #XXX is eot_ok needed?
                except:
                    exc, detail, tb = sys.exc_info()
                    self.transfer_failed(e_errors.ERROR, 'positioning error %s' % (detail,), error_source=TAPE)
                    return
                self.buffer.save_settings()
                bytes_read = 0L
                Trace.trace(20,"write_tape: header size %s" % (self.buffer.header_size,))
                #bytes_to_read = self.bytes_to_transfer + self.buffer.header_size
                bytes_to_read = self.bytes_to_transfer
                header_size = self.buffer.header_size
                # setup buffer for reads
                saved_wrapper = self.buffer.wrapper
                saved_sanity_bytes = self.buffer.sanity_bytes
                saved_complete_crc = self.buffer.complete_crc
                self.buffer.reset((self.buffer.sanity_bytes, self.buffer.sanity_crc), client_crc_on=1)
                self.buffer.set_wrapper(saved_wrapper)
                Trace.trace(22, "starting check after write, bytes_to_read=%s" % (bytes_to_read,))
                Trace.log(e_errors.INFO, "selective CRC check after writing file")
                driver = self.tape_driver
                first_block = 1
                while bytes_read < bytes_to_read:

                    nbytes = min(bytes_to_read - bytes_read, self.buffer.blocksize)
                    self.buffer.bytes_for_crc = nbytes
                    if bytes_read == 0 and nbytes<self.buffer.blocksize: #first read, try to read a whole block
                        nbytes = self.buffer.blocksize
                    try:
                        b_read = self.buffer.block_read(nbytes, driver)
                    except "CRC_ERROR":
                        exc, detail, tb = sys.exc_info()
                        Trace.handle_error(exc, detail, tb)
                        self.transfer_failed(e_errors.CRC_ERROR, detail, error_source=TAPE)
                        failed = 1
                        break
                    except:
                        exc, detail, tb = sys.exc_info()
                        Trace.handle_error(exc, detail, tb)
                        self.transfer_failed(e_errors.WRITE_ERROR, detail, error_source=TAPE)
                        failed = 1
                        break
                    if b_read <= 0:
                        self.transfer_failed(e_errors.WRITE_ERROR, "read returns %s" % (bytes_read,),
                                             error_source=TAPE)
                        failed = 1
                        break
                    if first_block:
                        bytes_to_read = bytes_to_read + header_size
                        first_block = 0
                    bytes_read = bytes_read + b_read
                    if bytes_read > bytes_to_read: #this is OK, we read a cpio trailer or something
                        bytes_read = bytes_to_read

                Trace.trace(22,"write_tape: read CRC %s write CRC %s"%
                            (self.buffer.complete_crc, saved_complete_crc))
                if failed: return
                if self.buffer.complete_crc != saved_complete_crc:
                    self.transfer_failed(e_errors.CRC_ERROR, None)
                    return
                Trace.log(e_errors.INFO, "selective CRC check after writing file cmpleted successfuly")
                self.buffer.restore_settings()
                # position to eod"
                self.tape_driver.seek(save_location, 0) #XXX is eot_ok
            if self.update_after_writing():
                self.transfer_completed()
                self.files_written_cnt = self.files_written_cnt + 1

            else:
                self.transfer_failed(e_errors.EPROTO)

    def read_tape(self):
        Trace.trace(8, "read_tape starting, bytes_to_read=%s" % (self.bytes_to_read,))
        if self.buffer.client_crc_on:
            # calculate checksum when reading from
            # tape (see comment in setup_transfer)
            do_crc = 1
        else:
            do_crc = 0
        driver = self.tape_driver
        failed = 0
        while self.state in (ACTIVE, DRAINING) and self.bytes_read < self.bytes_to_read:
            if self.buffer.full():
                Trace.trace(9, "read_tape: buffer full %s/%s, read %s/%s" %
                            (self.buffer.nbytes(), self.buffer.max_bytes,
                             self.bytes_read, self.bytes_to_read))
                self.buffer.read_ok.clear()
                self.buffer.read_ok.wait(1)
                continue
            
            nbytes = min(self.bytes_to_read - self.bytes_read, self.buffer.blocksize)
            self.buffer.bytes_for_crc = nbytes
            if self.bytes_read == 0 and nbytes<self.buffer.blocksize: #first read, try to read a whole block
                nbytes = self.buffer.blocksize

            bytes_read = 0
            try:
                bytes_read = self.buffer.block_read(nbytes, driver)
            except "CRC_ERROR":
                self.transfer_failed(e_errors.CRC_ERROR, None)
                failed = 1
                break
            except:
                exc, detail, tb = sys.exc_info()
                Trace.handle_error(exc, detail, tb)
                self.transfer_failed(e_errors.READ_ERROR, detail, error_source=TAPE)
                failed = 1
                break
            if bytes_read <= 0:
                self.transfer_failed(e_errors.READ_ERROR, "read returns %s" % (bytes_read,),
                                     error_source=TAPE)
                failed = 1
                break
            if self.bytes_read==0: #Handle variable-sized cpio header
                if len(self.buffer._buf) != 1:
                    Trace.log(e_errors.ERROR,
                              "read_tape: error skipping over cpio header, len(buf)=%s"%(len(self.buffer._buf)))
                b0 = self.buffer._buf[0]
                if len(b0) >= self.wrapper.min_header_size:
                    try:
                        header_size = self.wrapper.header_size(b0)
                    except (TypeError, ValueError), msg:
                        Trace.log(e_errors.ERROR,"Invalid header %s" %(b0[:self.wrapper.min_header_size]))
                        self.transfer_failed(e_errors.READ_ERROR, "Invalid file header", error_source=TAPE)
                        ##XXX NB: the client won't necessarily see this message since it's still trying
                        ## to recieve data on the data socket
                        failed = 1
                        break
                    self.buffer.header_size = header_size
                    self.bytes_to_read = self.bytes_to_read + header_size
            self.bytes_read = self.bytes_read + bytes_read
            if self.bytes_read > self.bytes_to_read: #this is OK, we read a cpio trailer or something
                self.bytes_read = self.bytes_to_read

            if not self.buffer.empty():
                self.buffer.write_ok.set()
        if failed: return
        if do_crc:
            Trace.trace(22,"read_tape: calculated CRC %s File DB CRC %s"%
                        (self.buffer.complete_crc, self.file_info['complete_crc']))
            if self.buffer.complete_crc != self.file_info['complete_crc']:
                self.transfer_failed(e_errors.CRC_ERROR, None)
                return

        Trace.notify("transfer %s %s %s media" %
                     (self.shortname, -self.bytes_read, self.bytes_to_read))            
        Trace.trace(8, "read_tape exiting, read %s/%s bytes" %
                    (self.bytes_read, self.bytes_to_read))
                
    def write_client(self):
        Trace.trace(8, "write_client starting, bytes_to_write=%s" % (self.bytes_to_write,))
        if not self.buffer.client_crc_on:
            # calculate checksum when writing to
            # the network (see comment in setup_transfer)
            # CRC when sending to the network if client does not CRC
            do_crc = 1
        else:
            do_crc = 0
        driver = self.net_driver
        #be careful about 0-length files
        if self.bytes_to_write > 0 and self.bytes_written == 0 and self.wrapper: #Skip over cpio or other headers
            while self.buffer.header_size is None and self.state in (ACTIVE, DRAINING):
                Trace.trace(8, "write_client: waiting for read_tape to set header info")
                self.buffer.write_ok.clear()
                self.buffer.write_ok.wait(1)
            # writing to "None" will discard headers, leaving stream positioned at
            # start of data
            self.buffer.stream_write(self.buffer.header_size, None)
            Trace.trace(8, "write_client: discarded %s bytes of header info"%(self.buffer.header_size))
        bytes_notified = 0L
        threshold = self.notify_transfer_threshold
        if threshold * 5 > self.bytes_to_write:
            threshold = self.bytes_to_write/5
        elif threshold * 100 < self.bytes_to_write:
            threshold = self.bytes_to_write/100

        failed = 0
           
        while self.state in (ACTIVE, DRAINING) and self.bytes_written < self.bytes_to_write:
            if self.buffer.empty():
                Trace.trace(9, "write_client: buffer empty, wrote %s/%s" %
                            (self.bytes_written, self.bytes_to_write))
                self.buffer.write_ok.clear()
                self.buffer.write_ok.wait(1)
                continue
                
            nbytes = min(self.bytes_to_write - self.bytes_written, self.buffer.blocksize)
            bytes_written = 0
            try:
                bytes_written = self.buffer.stream_write(nbytes, driver)
            except "CRC_ERROR":
                self.transfer_failed(e_errors.CRC_ERROR, None)
                failed = 1
                break
            except:
                exc, detail, tb = sys.exc_info()
                Trace.handle_error(exc, detail, tb)
                if self.state is not DRAINING: self.state = HAVE_BOUND
                # if state is DRAINING transfer_failed will set it to OFFLINE
                self.transfer_failed(e_errors.ENCP_GONE, detail)
                failed = 1
                break
            if bytes_written < 0:
                if self.state is not DRAINING: self.state = HAVE_BOUND
                # if state is DRAINING transfer_failed will set it to OFFLINE
                self.transfer_failed(e_errors.ENCP_GONE, "write returns %s"%(bytes_written,))
                failed = 1
                break
            if bytes_written != nbytes:
                pass #this is not unexpected, since we send with MSG_DONTWAIT
            self.bytes_written = self.bytes_written + bytes_written

            if not self.buffer.full():
                self.buffer.read_ok.set()

            if bytes_notified==0 or self.bytes_written - bytes_notified > threshold:
                bytes_notified = self.bytes_written
                #negative byte-count to indicate direction
                Trace.notify("transfer %s %s %s network" % (self.shortname, -self.bytes_written, self.bytes_to_write))

        Trace.trace(8, "write_client exiting: wrote %s/%s bytes" % (self.bytes_written, self.bytes_to_write))
        if failed: return
  
        if self.bytes_written == self.bytes_to_write:
            # check crc
            if do_crc:
                Trace.trace(22,"write_client: calculated CRC %s File DB CRC %s"%
                            (self.buffer.complete_crc, self.file_info['complete_crc']))
                if self.buffer.complete_crc != self.file_info['complete_crc']:
                    self.transfer_failed(e_errors.CRC_ERROR, None)
                    return
                
            self.transfer_completed()

        
    # the library manager has asked us to write a file to the hsm
    def write_to_hsm(self, ticket):
        Trace.log(e_errors.INFO, "WRITE_TO_HSM")
        self.setup_transfer(ticket, mode=WRITE)

    def update_volume_info(self, ticket):
        Trace.trace(20, "update_volume_info for %s. Current %s"%(ticket['external_label'],
                                                                 self.vol_info))
        if not self.vol_info:
            self.vol_info.update(self.vcc.inquire_vol(ticket['external_label']))
        else:
            if self.vol_info['external_label'] is not ticket['external_label']:
                Trace.log(e_errors.ERROR,"Library manager asked to update iformation for the wrong volume: %s, current %s" % (ticket['external_label'],self.vol_info['external_label']))
            else:
                self.vol_info.update(self.vcc.inquire_vol(ticket['external_label']))
            
            
    # the library manager has asked us to read a file from the hsm
    def read_from_hsm(self, ticket):
        Trace.log(e_errors.INFO,"READ FROM HSM")
        self.setup_transfer(ticket, mode=READ)

    def setup_transfer(self, ticket, mode):
        self.lock_state()
        save_state = self.state

        self.unique_id = ticket['unique_id']
        Trace.trace(10, "setup transfer")
        ## pprint.pprint(ticket)
        if save_state not in (IDLE, HAVE_BOUND):
            Trace.log(e_errors.ERROR, "Not idle %s" %(state_name(self.state),))
            self.return_work_to_lm(ticket)
            self.unlock_state()
            return 0

        self.state = SETUP
        
        #prevent a delayed dismount from kicking in right now
        if self.dismount_time:
            self.dismount_time = None
        self.unlock_state()
        
        ticket['mover']={}
        ticket['mover'].update(self.config)
        ticket['mover']['device'] = "%s:%s" % (self.config['host'], self.config['device'])

        self.current_work_ticket = ticket
        self.control_socket, self.client_socket = self.connect_client()
        
        Trace.trace(10, "client connect %s %s" % (self.control_socket, self.client_socket))
        if not self.client_socket:
            self.state = save_state
            ## Connecting to client failed
            if self.state is HAVE_BOUND:
                self.dismount_time = time.time() + self.default_dismount_delay
            self.update_lm(reset_timer=1)
            return 0

        self.t0 = time.time()

        ##all groveling around in the ticket should be done here
        fc = ticket['fc']
        vc = ticket['vc']
        self.vol_info.update(vc)
        self.file_info.update(fc)
        self.volume_family=vc['volume_family']
        ### cgw - abstract this to a check_valid_filename method of the driver ?
        if self.config['driver'] == "NullDriver": 
            filename = ticket['wrapper'].get("pnfsFilename",'')
            if "NULL" not in string.split(filename,'/'):
                ticket['status']=(e_errors.USERERROR, "NULL not in PNFS path")
                self.send_client_done(ticket, e_errors.USERERROR, "NULL not in PNFS path")
                self.state = save_state
                return 0
            wrapper_type = volume_family.extract_wrapper(self.vol_info['volume_family'])
            if ticket['work'] == 'write_to_hsm' and wrapper_type is not "null":
                ticket['status']=(e_errors.USERERROR, 'only "null" wrapper is allowed for NULL mover')
                self.send_client_done(ticket, e_errors.USERERROR,
                                      'only "null" wrapper is allowed for NULL mover')
                self.state = save_state
                return 0

        delay = 0
        if ticket['work'] == 'read_from_hsm':
            sanity_cookie = ticket['fc']['sanity_cookie']
        else:
            sanity_cookie = None
        
        if ticket.has_key('client_crc'):
            client_crc_on = ticket['client_crc']
        elif self.config['driver'] == "NullDriver":
            client_crc_on = 0
        else:
            client_crc_on = 1 # assume that client does CRC

        # if client_crc is ON:
        #    write requests -- calculate CRC when writing from memory to tape
        #    read requetsts -- calculate CRC when reading from tape to memory
        # if client_crc is OFF:
        #    write requests -- calculate CRC when writing to memory
        #    read requetsts -- calculate CRC when reading memory

        self.reset(sanity_cookie, client_crc_on)
        if ticket['encp'].has_key('delayed_dismount'):
            delay = 60 * int(ticket['encp']['delayed_dismount']) #XXX is this right? minutes?
                                                                  ##what does the flag really mean?
        self.delay = max(delay, self.default_dismount_delay)
        self.delay = min(self.delay, self.max_dismount_delay)
        self.fcc = file_clerk_client.FileClient(self.csc, bfid=0,
                                                server_address=fc['address'])
        self.vcc = volume_clerk_client.VolumeClerkClient(self.csc,
                                                         server_address=vc['address'])
        self.unique_id = ticket['unique_id']
        volume_label = fc['external_label']
        self.current_work_ticket = ticket
        if volume_label:
            self.vol_info.update(self.vcc.inquire_vol(volume_label))
        else:
            Trace.log(e_errors.ERROR, "setup_transfer: volume label=%s" % (volume_label,))
        if self.vol_info['status'][0] != 'ok':
            msg =  ({READ: e_errors.READ_NOTAPE, WRITE: e_errors.WRITE_NOTAPE}.get(
                mode, e_errors.EPROTO), self.vol_info['status'][1])
            Trace.log(e_errors.ERROR, "Volume clerk reply %s" % (msg,))
            self.send_client_done(ticket, msg[0], msg[1])
            self.state = save_state
            return 0
        
        self.buffer.set_blocksize(self.vol_info['blocksize'])
        self.wrapper = None
        self.wrapper_type = volume_family.extract_wrapper(self.volume_family)

        try:
            self.wrapper = __import__(self.wrapper_type + '_wrapper')
        except:
            exc, msg, tb = sys.exc_info()
            Trace.log(e_errors.ERROR, "error importing wrapper: %s %s" %(exc,msg))

        if not self.wrapper:
            msg = e_errors.EPROTO, "Illegal wrapper type %s" % (self.wrapper_type)
            Trace.log(e_errors.ERROR,  "%s" %(msg,))
            self.send_client_done(ticket, msg[0], msg[1])
            self.state = save_state
            return 0
        
        self.buffer.set_wrapper(self.wrapper)
        client_filename = ticket['wrapper'].get('fullname','?')
        pnfs_filename = ticket['wrapper'].get('pnfsFilename', '?')

        self.mode = mode
        self.bytes_to_transfer = long(fc['size'])
        self.bytes_to_write = self.bytes_to_transfer
        self.bytes_to_read = self.bytes_to_transfer

        ##NB: encp v2_5 supplies this information for writes but not reads. Somebody fix this!
        try:
            client_hostname = ticket['wrapper']['machine'][1]
        except KeyError:
            client_hostname = ''
        self.client_hostname = client_hostname
        if client_hostname:
            client_filename = client_hostname + ":" + client_filename

        if self.mode == READ:
            self.files = (pnfs_filename, client_filename)
            self.target_location = cookie_to_long(fc['location_cookie'])
            self.buffer.header_size = None
        elif self.mode == WRITE:
            self.files = (client_filename, pnfs_filename)
            if self.wrapper:
                self.header, self.trailer = self.wrapper.headers(ticket['wrapper'])
            else:
                self.header = ''
                self.trailer = ''
            self.buffer.header_size = len(self.header)
            self.buffer.trailer_size = len(self.trailer)
            self.bytes_to_write = self.bytes_to_write + len(self.header) + len(self.trailer)
            self.buffer.file_size = self.bytes_to_write
            self.target_location = None        

        if volume_label == self.current_volume: #no mount needed
            self.timer('mount_time')
            self.position_media(verify_label=0)
        else:
            self.run_in_thread('media_thread', self.mount_volume, args=(volume_label,),
                               after_function=self.position_media)
        
    def error(self, msg, err=e_errors.ERROR):
        self.last_error = (str(err), str(msg))
        Trace.log(e_errors.ERROR, str(msg)+ " state=ERROR")
        self.state = ERROR

    def broken(self, msg, err=e_errors.ERROR):
        self.set_sched_down()
        Trace.alarm(err, str(msg))
        self.error(msg, err)
        
    def position_media(self, verify_label=1):
        #At this point the media changer claims the correct volume is loaded; now position it
        label_tape = 0
        have_tape = 0
        err = None
        for retry_open in range(3):
            Trace.trace(10, "position media")
            have_tape = self.tape_driver.open(self.device, self.mode, retry_count=30)
            self.tape_driver.set_mode(blocksize = 0)
            if have_tape == 1:
                err = None
                break
            else:
                try:
                    Trace.log(e_errors.INFO, "rewind/retry")
                    r= self.tape_driver.close()
                    time.sleep(1)
                    ### XXX Yuk!! This is a total hack
                    p=os.popen("mt -f %s rewind 2>&1" % (self.device),'r')
                    r=p.read()
                    s=p.close()
                    ### r=self.tape_driver.rewind()
                    err = r
                    Trace.log(e_errors.INFO, "rewind/retry: mt rewind returns %s, status %s" % (r,s))
                    if s:
                        self.transfer_failed(e_errors.MOUNTFAILED, 'mount failure: %s' % (err,), error_source=ROBOT)
                        self.dismount_volume(after_function=self.idle)
                        return

                except:
                    exc, detail, tb = sys.exc_info()
                    err = detail
                    Trace.log(e_errors.ERROR, "rewind/retry: %s %s" % ( exc, detail))
        else:
            self.transfer_failed(e_errors.MOUNTFAILED, 'mount failure: %s' % (err,), error_source=ROBOT)
            self.dismount_volume(after_function=self.idle)
            return
        self.state = SEEK ##XXX start a timer here?
        eod = self.vol_info['eod_cookie']
        if eod=='none':
            eod = None
        volume_label = self.current_volume

        if self.mode is WRITE and eod is None:
            verify_label = 0
            label_tape = 1
        
        if self.mode is WRITE:
            if self.target_location is None:
                self.target_location = eod
            if self.target_location != eod:
                Trace.log(e_errors.ERROR, "requested write at location %s, eod=%s" %
                          (self.target_location, eod))
                return 0 # Can only write at end of tape

            if label_tape:
                ## new tape, label it
                ##  need to safeguard against relabeling here
                status = self.tape_driver.verify_label(None)
                Trace.trace(10, "verify label returns %s" % (status,))
                if status[0] == e_errors.OK:  #There is a label present!
                        msg = "volume %s already labeled %s" % (volume_label,status[1])
                        Trace.log(e_errors.ERROR, msg)
                        Trace.log(e_errors.ERROR, "marking %s noaccess" % (volume_label,))
                        self.vcc.set_system_noaccess(volume_label)
                        self.transfer_failed(e_errors.WRITE_VOL1_WRONG, msg, error_source=TAPE)
                        return 0

                self.tape_driver.rewind()
                vol1_label = 'VOL1'+ volume_label
                vol1_label = vol1_label+ (79-len(vol1_label))*' ' + '0'
                Trace.log(e_errors.INFO, "labeling new tape %s" % (volume_label,))
                self.tape_driver.write(vol1_label, 0, 80)
                self.tape_driver.writefm()
                eod = 1
                self.target_location = eod
                self.vol_info['eod_cookie'] = eod
                if self.driver_type == 'FTTDriver':
                    import ftt
                    stats = self.tape_driver.ftt.get_stats()
                    remaining = stats[ftt.REMAIN_TAPE]
                    if remaining is not None:
                        remaining = long(remaining)
                        self.vol_info['remaining_bytes'] = remaining * 1024L
                        ##XXX keep everything in KB?
                ret = self.vcc.set_remaining_bytes(volume_label,
                                                   self.vol_info['remaining_bytes'],
                                                   self.vol_info['eod_cookie'])
                if ret['status'][0] != e_errors.OK:
                    self.transfer_failed(ret['status'][0], ret['status'][1], error_source=TAPE)
                    return 0
                    

        if verify_label:
            status = self.tape_driver.verify_label(volume_label, self.mode)
            if status[0] != e_errors.OK:
                self.transfer_failed(status[0], status[1], error_source=TAPE)
                return 0
        location = cookie_to_long(self.target_location)
        self.run_in_thread('seek_thread', self.seek_to_location,
                           args = (location, self.mode==WRITE),
                           after_function=self.start_transfer)
        
        return 1
            
    def transfer_failed(self, exc=None, msg=None, error_source=None):
        broken = ""
        Trace.log(e_errors.ERROR, "transfer failed %s %s volume=%s location=%s" % (
            exc, msg, self.current_volume, self.current_location))
        Trace.notify("disconnect %s %s" % (self.shortname, self.client_ip))
        
        ### XXX translate this to an e_errors code?
        self.last_error = str(exc), str(msg)
        
        if self.state == ERROR:
            Trace.log(e_errors.ERROR, "Mover already in ERROR state %s, state=ERROR" % (msg,))
            return

        self.timer('transfer_time')
        if exc not in (e_errors.ENCP_GONE, e_errors.READ_VOL1_WRONG, e_errors.WRITE_VOL1_WRONG):
            self.consecutive_failures = self.consecutive_failures + 1
            if self.consecutive_failures >= self.max_consecutive_failures:
                broken =  "max_consecutive_failures (%d) reached" %(self.max_consecutive_failures)
            now = time.time()
            self.error_times.append(now)
            while self.error_times and now - self.error_times[0] > self.failure_interval:
                self.error_times.pop(0)
            if len(self.error_times) >= self.max_failures:
                broken =  "max_failures (%d) per failure_interval (%d) reached" % (self.max_failures,
                                                                                     self.failure_interval)
            ### network errors should not count toward rd_err, wr_err
            if self.mode == WRITE:
                self.vcc.update_counts(self.current_volume, wr_err=1, wr_access=1)
                #Heuristic: if tape is more than 90% full and we get a write error, mark it full
                try:
                    capacity = self.vol_info['capacity_bytes']
                    remaining = self.vol_info['remaining_bytes']
                    eod = self.vol_info['eod_cookie']
                    if remaining <= 0.1 * capacity:
                        Trace.log(e_errors.INFO,
                                  "heuristic: write error on vol %s, remaining=%s, capacity=%s, marking volume full"%
                                  (self.current_volume, remaining, capacity))
                        ret = self.vcc.set_remaining_bytes(self.current_volume, 0, eod, None)
                        if ret['status'][0] != e_errors.OK:
                            Trace.alarm(e_errors.ERROR, "set_remaining_bytes failed", ret)
                            broken = broken +  "set_remaining_bytes failed"
                                
                except:
                    exc, msg, tb = sys.exc_info()
                    Trace.log(e_errors.ERROR, "%s %s" % (exc, msg))
            else:
                self.vcc.update_counts(self.current_volume, rd_err=1, rd_access=1)       

            self.transfers_failed = self.transfers_failed + 1

        self.send_client_done(self.current_work_ticket, str(exc), str(msg))
        self.net_driver.close()
        self.need_lm_update = (1, ERROR, 1, error_source)    

        if broken:
            self.broken(broken)
            return
        
        save_state = self.state
        self.dismount_volume()

        if save_state == DRAINING:
            self.state = OFFLINE
        else:
            self.maybe_clean()
            self.idle()
            
        #self.delayed_update_lm() Why do we need delayed udpate AM 01/29/01
        #self.update_lm()
        #self.need_lm_update = (1, 0, None)    
        
    def transfer_completed(self):
        self.consecutive_failures = 0
        Trace.log(e_errors.INFO, "transfer complete volume=%s location=%s"%(
            self.current_volume, self.current_location))
        Trace.notify("disconnect %s %s" % (self.shortname, self.client_ip))
        if self.mode == WRITE:
            self.vcc.update_counts(self.current_volume, wr_access=1)
        else:
            self.vcc.update_counts(self.current_volume, rd_access=1)
        self.transfers_completed = self.transfers_completed + 1
        self.timer('transfer_time')
        self.net_driver.close()
        self.current_location = self.tape_driver.tell()
        now = time.time()
        self.dismount_time = now + self.delay
        self.send_client_done(self.current_work_ticket, e_errors.OK)
        ######### AM 01/30/01
        ### do not update lm as in a child thread
        # self.update_lm(reset_timer=1)
        ##########################
        
        if self.state == DRAINING:
            self.dismount_volume()
            self.state = OFFLINE
        else:
            self.state = HAVE_BOUND
            if self.maybe_clean():
                self.state = IDLE
        self.need_lm_update = (1, None, 1, None)
        ######### AM 01/30/01
        ### do not update lm in child a thread
        #if self.state == HAVE_BOUND:
        #    self.update_lm(reset_timer=1)
        ###############################
        
        #self.delayed_update_lm() Why do we need delayed udpate AM 01/29/01
        #self.update_lm()
            
    def maybe_clean(self):
        needs_cleaning = self.tape_driver.get_cleaning_bit()
        did_cleaning = 0
        if needs_cleaning:
            if not self.do_cleaning:
                Trace.log(e_errors.INFO, "cleaning bit set but automatic cleaning disabled")
                return 0
            Trace.log(e_errors.INFO, "initiating automatic cleaning")
            did_cleaning = 1
            save_state = self.state
            if save_state == HAVE_BOUND:
                self.dismount_volume()
                save_state = IDLE
            self.state = CLEANING
            self.mcc.doCleaningCycle(self.config)
            self.state = save_state
            Trace.log(e_errors.INFO, "cleaning complete")
        needs_cleaning = 0
        return did_cleaning
        
    def update_after_writing(self):
        previous_eod = cookie_to_long(self.vol_info['eod_cookie'])
        self.current_location = self.tape_driver.tell()
        if self.current_location <= previous_eod:
            Trace.log(e_errors.ERROR, " current location %s <= eod %s" %
                      (self.current_location, previous_eod))
            return 0

        r0 = self.vol_info['remaining_bytes']  #value prior to this write
        r1 = r0 - self.bytes_written           #value derived from simple subtraction
        r2 = r1                                #value reported from drive, if possible
        ## XXX OO: this should be a driver method
        if self.driver_type == 'FTTDriver':
            import ftt
            try:
                stats = self.tape_driver.ftt.get_stats()
                r2 = long(stats[ftt.REMAIN_TAPE]) * 1024L
            except:
                Trace.log(e_errors.ERROR, "ftt.get_stats cannot get remaining capacity")

        capacity = self.vol_info['capacity_bytes']
        if r1 <= 0.1 * capacity:  #do not allow remaining capacity to decrease in the "near-EOT" regime
            remaining = min(r1, r2)
        else:                     #trust what the drive tells us, as long as we are under 90% full
            remaining = r2

        self.vol_info['remaining_bytes']=remaining
        eod = loc_to_cookie(self.current_location)
        self.vol_info['eod_cookie'] = eod
        sanity_cookie = (self.buffer.sanity_bytes,self.buffer.sanity_crc)
        complete_crc = self.buffer.complete_crc
        fc_ticket = {  'location_cookie': loc_to_cookie(previous_eod),
                       'size': self.bytes_to_transfer,
                       'sanity_cookie': sanity_cookie,
                       'external_label': self.current_volume,
                       'complete_crc': complete_crc}
        ##  HACK:  store 0 to database if mover is NULL
        if self.config['driver']=='NullDriver':
            fc_ticket['complete_crc']=0L
            fc_ticket['sanity_cookie']=(self.buffer.sanity_bytes,0L)
        fcc_reply = self.fcc.new_bit_file({'work':"new_bit_file",
                                            'fc'  : fc_ticket
                                            })
        if fcc_reply['status'][0] != e_errors.OK:
            Trace.log(e_errors.ERROR,
                       "cannot assign new bfid")
            self.transfer_failed(e_errors.ERROR,"Cannot assign new bit file ID")
            #XXX exception?
            return 0
        ## HACK: restore crc's before replying to caller
        fc_ticket = fcc_reply['fc']
        fc_ticket['sanity_cookie'] = sanity_cookie
        fc_ticket['complete_crc'] = complete_crc 
        bfid = fc_ticket['bfid']
        self.current_work_ticket['fc'] = fc_ticket
        Trace.log(e_errors.INFO,"set remaining: %s %s %s" %(self.current_volume, remaining, eod))
        reply = self.vcc.set_remaining_bytes(self.current_volume, remaining, eod, bfid)
        if reply['status'][0] != e_errors.OK:
            self.transfer_failed(reply['status'][0], reply['status'][1], error_source=TAPE)
            return 0
        self.vol_info.update(reply)
        if self.current_volume: ####XXX CGW why do this, set_remaining returns volume info....
            self.vol_info.update(self.vcc.inquire_vol(self.current_volume))  
        else:
            Trace.log(e_errors.ERROR, "update_after_writing: volume=%s" % (self.current_volume,))
        return 1

    def malformed_ticket(self, ticket, expected_keys=None):
        msg = "Missing keys "
        if expected_keys is not None:
            msg = "%s %s"(msg, expected_keys)
        msg = "%s %s"%(msg, ticket)
        Trace.log(e_errors.ERROR, msg)

    def send_client_done(self, ticket, status, error_info=None):
        if self.control_socket is None:
            return
        ticket['status'] = (status, error_info)
        try:
            callback.write_tcp_obj(self.control_socket, ticket)
        except:
            exc, detail, tb = sys.exc_info()
            Trace.log(e_errors.ERROR, "error in send_client_done: %s" % (detail,))
        if self.control_socket:
            self.control_socket.close()
        self.control_socket = None
        return
            
    def connect_client(self):
        Trace.trace(10, "connecting to client")
        # cgw - Should this thread out?
        try:
            ticket = self.current_work_ticket
            data_ip=self.config.get("data_ip",None)
            host, port, listen_socket = callback.get_callback(ip=data_ip)
            listen_socket.listen(1)
            ticket['mover']['callback_addr'] = (host,port) #client expects this

            control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            flags = fcntl.fcntl(control_socket.fileno(), FCNTL.F_GETFL)
            fcntl.fcntl(control_socket.fileno(), FCNTL.F_SETFL, flags | FCNTL.O_NONBLOCK)
            Trace.trace(10, "connecting to %s" % (ticket['callback_addr'],))
            for retry in xrange(60):
                try:
                    control_socket.connect(ticket['callback_addr'])
                    break
                except socket.error, detail:
                    Trace.log(e_errors.ERROR, "%s %s" %
                              (detail, ticket['callback_addr']))
                    if detail[0] == errno.ECONNREFUSED:
                        return None, None
                    elif host_type()==IRIX and detail[0]==errno.EISCONN:
                        break #This is not an error! The connection succeeded.
                    time.sleep(1)
            else:
                Trace.log(e_errors.ERROR, "timeout connecting to %s" %
                          (ticket['callback_addr'],))
                return None, None
            fcntl.fcntl(control_socket.fileno(), FCNTL.F_SETFL, flags)
            Trace.trace(10, "connected")
            try:
                callback.write_tcp_obj(control_socket, ticket)
            except:
                exc, detail, tb = sys.exc_info()
                Trace.log(e_errors.ERROR,"error in connect_client: %s" % (detail,))
                return None, None
            # we expect a prompt call-back here
            Trace.trace(10, "select: listening for client callback")
            read_fds,write_fds,exc_fds=select.select([listen_socket],[],[],60) # one minute timeout
            Trace.trace(10, "select returned %s" % ((listen_socket in read_fds),))
            if listen_socket in read_fds:
                Trace.trace(10, "accepting client connection")
                client_socket, address = listen_socket.accept()
                if not hostaddr.allow(address):
                    client_socket.close()
                    listen_socket.close()
                    return None, None
                if data_ip:
                    interface=hostaddr.interface_name(data_ip)
                    if interface:
                        status=socket_ext.bindtodev(client_socket.fileno(),interface)
                        if status:
                            Trace.log(e_errors.ERROR, "bindtodev(%s): %s"%(interface,os.strerror(status)))

                listen_socket.close()
                self.client_ip = address[0]
                Trace.notify("connect %s %s" % (self.shortname, self.client_ip))
                self.net_driver.fdopen(client_socket)
                return control_socket, client_socket
            else:
                Trace.log(e_errors.ERROR, "timeout on waiting for client connect")
                return None, None
        except:
            exc, msg, tb = sys.exc_info()
            Trace.log(e_errors.ERROR, "connect_client:  %s %s %s"%
                      (exc, msg, traceback.format_tb(tb)))
            return None, None 
    
    def format_lm_ticket(self, state=None, error_info=None, returned_work=None, error_source=None):
        status = e_errors.OK, None
        work = None
        if state is None:
            state = self.state
        Trace.trace(20,"format_lm_ticket: state %s"%(state,))
        if state is IDLE:
            work = "mover_idle"
        elif state in (HAVE_BOUND,):
            work = "mover_bound_volume"
        elif state in (ACTIVE, SETUP, SEEK, DRAINING, CLEANING, MOUNT_WAIT, DISMOUNT_WAIT):
            work = "mover_busy"
            if error_info:
                status = error_info
        elif state in (ERROR, OFFLINE):
            work = "mover_error"  ## XXX If I'm offline should I send mover_error? I don't think so....
            if error_info is None:
                status = self.last_error
            else:
                status = error_info
        if work is None:
            Trace.log(e_errors.ERROR, "state: %s work: %s" % (state_name(state),work))

        if not status:
            status = e_errors.OK, None
            
        if type(status) != type(()) or len(status)!=2:
            Trace.log(e_errors.ERROR, "status should be 2-tuple, is %s" % (status,))
            status = (status, None)

        if self.unique_id and state in (IDLE, HAVE_BOUND):
            ## If we've been idle for more than 15 minutes, force the LM to clear
            ## any entry for this mover in the work_at_movers.  Yes, this is a
            ## kludge, but it keeps the system from getting completely hung up
            ## if the LM doesn't realize we've finished a transfer.
            now = time.time()
            if time.time() - self.state_change_time > 900:
                self.unique_id = None

        Trace.trace(20, "format_lm_ticket: volume info %s"%(self.vol_info,))
        if not self.vol_info:
            volume_status = (['none', 'none'], ['none','none'])
        else:
            volume_status = (self.vol_info.get('system_inhibit',['Unknown', 'Unknown']),
                             self.vol_info.get('user_inhibit',['Unknown', 'Unknown']))
            
        ticket =  {
            "mover":  self.name,
            "address": self.address,
            "external_label":  self.current_volume,
            "current_location": loc_to_cookie(self.current_location),
            "read_only" : 0, ###XXX todo: multiple drives on one scsi bus, write locking
            "returned_work": returned_work,
            "state": state_name(self.state),
            "status": status,
            "volume_family": self.volume_family,
            "volume_status": volume_status,
            "operation": mode_name(self.mode),
            "error_source": error_source,
            "unique_id": self.unique_id,
            "work": work,
            }
        return ticket

    def run_in_thread(self, thread_name, function, args=(), after_function=None):
        thread = getattr(self, thread_name, None)
        for wait in range(5):
            if thread and thread.isAlive():
                Trace.trace(20, "thread %s is already running, waiting %s" % (thread_name, wait))
                time.sleep(1)
        if thread and thread.isAlive():
                Trace.log(e_errors.ERROR, "thread %s is already running" % (thread_name))
                return -1
        if after_function:
            args = args + (after_function,)
        thread = threading.Thread(group=None, target=function,
                                  name=thread_name, args=args, kwargs={})
        setattr(self, thread_name, thread)
        try:
            thread.start()
        except:
            exc, detail, tb = sys.exc_info()
            Trace.log(e_errors.ERROR, "starting thread %s: %s" % (thread_name, detail))
        return 0
    
    def dismount_volume(self, after_function=None):
        broken = ""
        self.dismount_time = None
        if not self.do_eject:
            ### AM I do not know if this is correct but it does what it supposed to
            ### Do not eject if specified
            Trace.log(e_errors.INFO, "Do not eject specified")
            self.state = HAVE_BOUND
            return
            self.current_volume = None
            self.volume_family = None
            if after_function:
                after_function()

        self.state = DISMOUNT_WAIT

        ejected = self.tape_driver.eject()
        if ejected == -1:
            if self.can_force_eject:
                # try to unload tape if robot is STK. It can do this
                Trace.log(e_errors.INFO,"Eject failed. For STK robot will try to unload anyway")
            else:
                
                broken = "Cannot eject tape"

                if self.current_volume:
                    try:
                        self.vcc.set_system_noaccess(self.current_volume)
                    except:
                        exc, msg, tb = sys.exc_info()
                        broken = broken + "set_system_noaccess failed: %s %s" %(exc, msg)                

                self.broken(broken)

            return
        self.tape_driver.close()
        Trace.notify("unload %s %s" % (self.shortname, self.current_volume))
        Trace.log(e_errors.INFO, "dismounting %s" %(self.current_volume,))
        self.last_volume = self.current_volume
        self.last_location = self.current_location

        if not self.vol_info.get('external_label'):
            if self.vcc:
                if self.current_volume:
                    v = self.vcc.inquire_vol(self.current_volume)
                    if type(v) is type({}) and v.has_key('status') and v['status'][0]==e_errors.OK:
                        self.vol_info.update(v)
                    else:
                        Trace.log(e_errors.ERROR, "dismount_volume: inquire_vol(%s)->%s" %
                                  (self.current_volume, v))
                else:
                    Trace.log(e_errors.ERROR, "dismount_volume: volume=%s" % (self.current_volume,))

        if not self.vol_info.get('external_label'):
            if self.current_volume:
                self.vol_info['external_label'] = self.current_volume
            else:
                self.vol_info['external_label'] = "Unknown"

        if not self.vol_info.get('media_type'):
            self.vol_info['media_type'] = self.media_type #kludge

        mcc_reply = self.mcc.unloadvol(self.vol_info, self.name, self.mc_device)

        status = mcc_reply.get('status')
        if status and status[0]==e_errors.OK:
            self.current_volume = None
            if after_function:
                Trace.trace(20,"after function %s" % (after_function,))
                after_function()

        ###XXX aml-specific hack! Media changer should provide a layer of abstraction
        ### on top of media changer error returns, but it doesn't  :-(
        elif status[-1] == "the drive did not contain an unloaded volume":
            self.idle()
        else:
##            self.error(status[-1], status[0])
            
            broken = "dismount failed: %s %s" %(status[-1], status[0])
            if self.current_volume:
                try:
                    self.vcc.set_system_noaccess(self.current_volume)
                except:
                    exc, msg, tb = sys.exc_info()
                    broken = broken + "set_system_noaccess failed: %s %s" %(exc, msg)
            self.broken(broken)        
        return
    
    def mount_volume(self, volume_label, after_function=None):
        broken = ""
        self.dismount_time = None
        if self.current_volume:
            self.dismount_volume()

        self.state = MOUNT_WAIT
        self.current_volume = volume_label


        # XXX DEBUG Block of code to get more info on why label is missing on some mounts
        if not self.vol_info.get('external_label'):
            Trace.log(e_errors.ERROR, "mount_volume: no external label in vol_info.  volume_label=%s" % (volume_label,))
            if self.vcc:
                if self.current_volume:
                    v = self.vcc.inquire_vol(self.current_volume)
                    if type(v) is type({}) and v.has_key('status') and v['status'][0]==e_errors.OK:
                        self.vol_info.update(v)
                    else:
                        Trace.log(e_errors.ERROR, "mount_volume: inquire_vol(%s)->%s" %
                                  (self.current_volume, v))
                else:
                    Trace.log(e_errors.ERROR, "mount_volume: no self.current_volume self.current_volue=%s volume_label=%s" %
                              (self.current_volume,volume_label))
            else:
                Trace.log(e_errors.ERROR, "mount_volume: no self.vcc")

        if not self.vol_info.get('external_label'):
            if self.current_volume:
                self.vol_info['external_label'] = self.current_volume
            else:
                self.vol_info['external_label'] = "Unknown"

        if not self.vol_info.get('media_type'):
            self.vol_info['media_type'] = self.media_type #kludge
        # XXX END DEBUG Block of code to get more info on why label is missing on some mounts


        Trace.notify("loading %s %s" % (self.shortname, volume_label))        
        Trace.log(e_errors.INFO, "mounting %s"%(volume_label,),
                  msg_type=Trace.MSG_MC_LOAD_REQ)
        self.timer('mount_time')
        
        mcc_reply = self.mcc.loadvol(self.vol_info, self.name, self.mc_device)
        status = mcc_reply.get('status')
        Trace.trace(10, 'mc replies %s' % (status,))

        #Do another query volume, just to make sure its status has not changed
        self.vol_info.update(self.vcc.inquire_vol(volume_label))

        
        if status and status[0] == e_errors.OK:
            Trace.notify("loaded %s %s" % (self.shortname, volume_label))        
            Trace.log(e_errors.INFO, "mounted %s"%(volume_label,),
                  msg_type=Trace.MSG_MC_LOAD_DONE)

            if self.mount_delay:
                Trace.trace(25, "waiting %s seconds after mount"%(self.mount_delay,))
                time.sleep(self.mount_delay)
            if after_function:
                Trace.trace(10, "mount: calling after function")
                after_function()
        else: #Mount failure, do not attempt to recover
            self.last_error = status
##            "I know I'm swinging way to far to the right" - Jon
##            Trace.log(e_errors.ERROR, "mount %s: %s, dismounting" % (volume_label, status))
##            self.state = DISMOUNT_WAIT
##            self.transfer_failed(e_errors.MOUNTFAILED, 'mount failure %s' % (status,), error_source=ROBOT)
##            self.dismount_volume(after_function=self.idle)
            Trace.log(e_errors.ERROR, "mount %s: %s; broken" % (volume_label, status))
            broken = "mount %s failed: %s" % (volume_label, status)
            try:
                self.vcc.set_system_noaccess(volume_label)
            except:
                exc, msg, tb = sys.exc_info()
                broken = broken + "set_system_noaccess failed: %s %s" %(exc, msg)
            self.broken(broken)
            
    def seek_to_location(self, location, eot_ok=0, after_function=None): #XXX is eot_ok needed?
        Trace.trace(10, "seeking to %s, after_function=%s"%(location,after_function))
        failed=0
        try:
            self.tape_driver.seek(location, eot_ok) #XXX is eot_ok needed?
        except:
            exc, detail, tb = sys.exc_info()
            self.transfer_failed(e_errors.ERROR, 'positioning error %s' % (detail,), error_source=TAPE)
            failed=1
        self.timer('seek_time')
        self.current_location = self.tape_driver.tell()
        if self.mode is WRITE:
            previous_eod = cookie_to_long(self.vol_info['eod_cookie'])
            Trace.trace(10,"seek_to_location: current location %s, eod %s"%
                        (self.current_location, previous_eod))
            # compare location reported by driver with eod cookie
            if self.current_location != previous_eod:
                Trace.log(e_errors.ERROR, " current location %s != eod %s" %
                          (self.current_location, previous_eod))
                detail = "wrong location %s, eod %s"%(self.current_location, previous_eod)
                self.transfer_failed(e_errors.WRITE_ERROR, detail, error_source=TAPE)
                failed = 1
        if after_function and not failed:
            Trace.trace(10, "seek calling after function %s" % (after_function,))
            after_function()

    def start_transfer(self):
        Trace.trace(10, "start transfer")
        #If we've gotten this far, we've mounted, positioned, and connected to the client.
        #Just start up the work threads and watch the show...
        self.state = ACTIVE
        if self.mode is WRITE:
            self.run_in_thread('net_thread', self.read_client)
            self.run_in_thread('tape_thread', self.write_tape)
        elif self.mode is READ:
            self.run_in_thread('tape_thread', self.read_tape)
            self.run_in_thread('net_thread', self.write_client)
        else:
            self.transfer_failed(e_errors.ERROR, "invalid mode %s" % (self.mode,))
                
    def status(self, ticket):
        now = time.time()
        status_info = (e_errors.OK, None)
        if self.state == ERROR:
            status_info = self.last_error
        tick = { 'status'       : status_info,
                 'drive_sn'     : self.config['serial_num'],
                 'drive_vendor' : self.config['vendor_id'],
                 'drive_id'     : self.config['product_id'],
                 #
                 'state'        : state_name(self.state),
                 'transfers_completed'     : self.transfers_completed,
                 'transfers_failed': self.transfers_failed,
                 'bytes_read'     : self.bytes_read,
                 'bytes_written'     : self.bytes_written,
                 'bytes_buffered' : self.buffer.nbytes(),
                 # from "work ticket"
                 'bytes_to_transfer': self.bytes_to_transfer,
                 'files'        : self.files,
                 'last_error': self.last_error,
                 'mode'         : mode_name(self.mode),
                 'current_volume': self.current_volume,
                 'current_location': self.current_location,
                 'last_volume' : self.last_volume,
                 'last_location': self.last_location,
                 'time_stamp'   : now,
                 'time_in_state': now - self.state_change_time,
                 'buffer_min': self.buffer.min_bytes,
                 'buffer_max': self.buffer.max_bytes,
                 'rate of network': self.net_driver.rates()[0],
                 'rate of tape': self.tape_driver.rates()[0],
                 'default_dismount_delay': self.default_dismount_delay,
                 'max_dismount_delay': self.max_dismount_delay,
                 }
        if self.state is HAVE_BOUND and self.dismount_time and self.dismount_time>now:
            tick['will dismount'] = 'in %.1f seconds' % (self.dismount_time - now)
            
        self.reply_to_caller(tick)
        return

    def timer(self, key):
        if not self.current_work_ticket:
            return
        ticket = self.current_work_ticket
        if not ticket.has_key('times'):
            ticket['times']={}
        now = time.time()
        ticket['times'][key] = now - self.t0
        self.t0 = now
    
    def lockfile_name(self):
        d=os.environ.get("ENSTORE_TMP","/tmp")
        return os.path.join(d, "mover_lock")
        
    def create_lockfile(self):
        filename=self.lockfile_name()
        try:
            f=open(filename,'w')
            f.write('locked\n')
            f.close()
        except (OSError, IOError):
            Trace.log(e_errors.ERROR, "Cannot write %s"%(filename,))
            
    def remove_lockfile(self):
        filename=self.lockfile_name()
        try:
            os.unlink(filename)
        except (OSError, IOError):
            Trace.log(e_errors.ERROR, "Cannot unlink %s"%(filename,))

    def check_lockfile(self):
        return os.path.exists(self.lockfile_name())
        
    def start_draining(self, ticket):       # put itself into draining state
        if self.state is ACTIVE:
            self.state = DRAINING
        elif self.state in (IDLE, ERROR):
            self.state = OFFLINE
        elif self.state is HAVE_BOUND:
            self.state = DRAINING # XXX CGW should dismount here. fix this
        self.create_lockfile()
        out_ticket = {'status':(e_errors.OK,None)}
        self.reply_to_caller(out_ticket)
        return

    def stop_draining(self, ticket):        # put itself into draining state
        if self.state != OFFLINE:
            out_ticket = {'status':("EPROTO","Not in draining state")}
            self.reply_to_caller(out_ticket)
            return
        out_ticket = {'status':(e_errors.OK,None)}
        self.reply_to_caller(out_ticket)
        self.remove_lockfile()
        self.idle()
        
    def clean_drive(self, ticket):
        save_state = self.state
        if self.state not in (IDLE, OFFLINE):
            ret = {'status':("EPROTO", "Cleaning not allowed in %s state" % (state_name(self.state)))}
        else:
            self.state = CLEANING
            ret = self.mcc.doCleaningCycle(self.config)
            self.state = save_state
        self.reply_to_caller(ret)
        

class MoverInterface(generic_server.GenericServerInterface):

    def __init__(self):
        # fill in the defaults for possible options
        generic_server.GenericServerInterface.__init__(self)

    #  define our specific help
    def parameters(self):
        return 'mover_name'

    # parse the options like normal but make sure we have a mover
    def parse_options(self):
        interface.Interface.parse_options(self)
        # bomb out if we don't have a mover
        if len(self.args) < 1 :
            self.missing_parameter(self.parameters())
            self.print_help(),
            os._exit(1)
        else:
            self.name = self.args[0]


if __name__ == '__main__':            

    if len(sys.argv)<2:
        sys.argv=["python", "null.mover"] #REMOVE cgw
    # get an interface, and parse the user input

    intf = MoverInterface()
    mover =  Mover((intf.config_host, intf.config_port), intf.name)
    mover.handle_generic_commands(intf)
    mover.start()
    
    while 1:
        try:
            mover.serve_forever()
        except SystemExit:
            Trace.log(e_errors.INFO, "mover %s exiting." % (mover.name,))
            os._exit(0)
            break
        except:
            try:
                exc, msg, tb = sys.exc_info()
                full_tb = traceback.format_exception(exc,msg,tb)
                for l in full_tb:
                    Trace.log(e_errors.ERROR, l[:-1], {}, "TRACEBACK")
                Trace.log(e_errors.INFO, "restarting after exception")
            except:
                pass

    Trace.log(e_errors.INFO, 'ERROR returned from serve_forever')
