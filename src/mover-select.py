#!/usr/bin/env python

# $Id$


import os

# python modules
import sys
import os
import errno
import pprint
import socket
import signal				
import time				
import string				
import select
import exceptions
import traceback

# enstore modules

import setpath
import generic_server
import interface
import dispatching_worker
import volume_clerk_client		
import file_clerk_client		
import media_changer_client		
import callback				
import Trace

import e_errors
import udp_client
import socket_ext
import hostaddr


def print_args(*args):
    print args

verbose=10
    
Trace.trace = print_args

class MoverError(exceptions.Exception):
    def __init__(self, arg):
        exceptions.Exception.__init__(self,arg)


#states
IDLE, MOUNT_WAIT, ACTIVE, HAVE_BOUND, DISMOUNT_WAIT, DRAINING, OFFLINE, CLEANING, ERROR = range(9)

_state_names=['IDLE', 'MOUNT_WAIT', 'ACTIVE', 'HAVE_BOUND', 'DISMOUNT_WAIT',
             'DRAINING', 'OFFLINE', 'CLEANING', 'ERROR']
def state_name(state):
    return _state_names[state]

#modes
READ, WRITE = range(2)

def  mode_name(mode):
    if mode is None:
        return None
    else:
        return ['READ','WRITE'][mode]

KB=1L<<10
MB=1L<<20
GB=1L<<30

class Buffer:
    def __init__(self, blocksize, min_bytes = 0, max_bytes = 1*MB):
        self.blocksize = blocksize
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes
        self._buf = []
        self._buf_bytes = 0L
        self._freelist = []
        self._work_block = None
        self._readptr = 0
        self._writeptr = 0
        
    def nbytes(self):
        if self._work_block:
            if self._readptr:
                return self._buf_bytes + self._readptr
            else:
                return self._buf_bytes + len(self._work_block) - self._writeptr
        else:
            return self._buf_bytes
        
    def full(self):
        return self.nbytes() > self.max_bytes
    
    def empty(self):
        return self.nbytes() == 0
    
    def set_min_bytes(self, min_bytes):
        self.min_bytes = min_bytes
        
    def set_blocksize(self, blocksize):
        if blocksize == self.blocksize:
            return
        if not self.empty():
            raise "Buffer error: changing blocksize of nonempty buffer"
        self._freelist = []
        self.blocksize = blocksize
        
    def low(self):
        return self.nbytes() <= self.min_bytes
    
    def push(self, data):
        self._buf.append(data)
        self._buf_bytes = self._buf_bytes + len(data)
        
    def pull(self):
        if not self._buf:
            if self._work_block and self._readptr>0:
                data = self._work_block[:self._readptr]
                self._work_block = None
                self._readptr = 0
                return data
            else:
                raise ValueError, "buffer is empty"
        data = self._buf.pop(0)
        self._buf_bytes = self._buf_bytes - len(data)
        return data
    
    def reset(self):
        self._buf = []
        self._freelist = []
        self._buf_bytes = 0
        self._work_block = None
        self._readptr = 0
        self._writeptr = 0

    def nonzero(self):
        return self.nbytes() > 0
    
    def __repr__(self):
        return "Buffer %s  %s  %s" % (self.min_bytes, self._buf_bytes, self.max_bytes)

    def block_read(self, nbytes, driver):
        space = self._getspace()
        bytes_read = driver.read(space, 0, nbytes)
        if bytes_read == nbytes: #normal case
            self.push(space)
            return bytes_read
        elif bytes_read<=0: #error
            return bytes_read #XXX or raise an exception?
        else: #partial block read
            partial=space[:bytes_read]
            self.push(partial)
            self._freespace(space)
            return bytes_read

    def block_write(self, nbytes, driver):
        data = self.pull() 
        if len(data)!=nbytes:
            raise ValueError, "asked to write %s bytes, buffer has %s" % (nbytes, len(data))
        bytes_written = driver.write(data, 0, nbytes)
        if bytes_written == nbytes: #normal case
            self._freespace(data)
            return bytes_written
        else: #XXX raise an exception?
            self._freespace(data)
            return bytes_written
        
    def stream_read(self, nbytes, driver):
        if not self._work_block:
            self._work_block = self._getspace()
            self._readptr = 0
        bytes_to_read = min(self.blocksize - self._readptr, nbytes)
        bytes_read = driver.read(self._work_block, self._readptr, bytes_to_read)
        self._readptr = self._readptr + bytes_read
        if self._readptr == self.blocksize: #we filled up  a block
            self.push(self._work_block)
            self._work_block = None
            self._readptr = 0
        return bytes_read
        
    def stream_write(self, nbytes, driver):
        if self.empty():
            if verbose: print "stream_write: buffer empty"
            return 0
        if not self._work_block:
            self._work_block = self.pull()
            self._writeptr = 0
        bytes_to_write = min(len(self._work_block)-self._writeptr, nbytes)
        bytes_written = driver.write(self._work_block, self._writeptr, bytes_to_write)

        self._writeptr = self._writeptr + bytes_written
        if self._writeptr == len(self._work_block): #finished sending out this block
            self._freespace(self._work_block)
            self._work_block = None
            self._writeptr = 0
        return bytes_written
    
    def _getspace(self):
        if self._freelist:
            return self._freelist.pop(0)
        else:
            return '\0' * self.blocksize
    def _freespace(self, s):
        self._freelist.append(s)

        
class Mover(dispatching_worker.DispatchingWorker,
            generic_server.GenericServer):

    def __init__(self, csc_address, name):

        self.name = name
        self.t0 = time.time()
        
        generic_server.GenericServer.__init__(self, csc_address, name)

        self.config = self.csc.get( name )
        if self.config['status'][0] != 'ok':
            raise MoverError('could not start mover %s: %s'%(name, self.config['status']))

        logname = self.config.get('logname', name)
        Trace.init(logname)
        
        self.address = (self.config['hostip'], self.config['port'])

        self.do_eject = 1
        if self.config.has_key('do_eject'):
            if self.config['do_eject'][0] in ('n','N'):
                self.do_eject = 0

        self.default_dismount_delay = self.config.get('dismount_delay', 30)
        if self.default_dismount_delay < 0:
            self.default_dismount_delay = 31536000 #1 year
            
        self.min_buffer = self.config.get('min_buffer', 16*MB)
        self.max_buffer = self.config.get('max_buffer', 64*MB)
        self.fast_rate = self.config.get('fast_rate', 10*MB)
        
        self.buffer = Buffer(0, self.min_buffer, self.max_buffer)
            
        self.udpc =  udp_client.UDPClient()
        self.state = IDLE
        self.last_error = ()
        if self.check_lockfile():
            self.state = OFFLINE

        self.current_location = 0L
        self.current_volume = None #external label of current mounted volume
	
	self.last_location = 0L
	self.last_volume = None
	
        self.mode = None # READ or WRITE
        self.bytes_to_transfer = 0L
        self.bytes_read = 0L
        self.bytes_written = 0L

        self.next_volume = None # external label of pending (MC) volume
        self.volume_family = None 
        self.volume_status = (['none', 'none'], ['none', 'none'])
        self.files = ('','')
        self.transfers_completed = 0
        self.transfers_failed = 0
        self.current_work_ticket = {}

        self.vol_info = {}
        

        self.dismount_time = None
        self.delay = 0
        

##        self.mcc = media_changer_client.MediaChangerClient( self.csc,
##                                             self.config['media_changer'] )


        self.read_error = [0,0]         # error this vol ([0]) and last vol ([1])
        self.crc_flag = 1
        self.config['device'] = os.path.expandvars( self.config['device'] )

        import net_driver
        self.net_driver = net_driver.NetDriver()
        self.client_socket = None


        self.config['name']=self.name 
        self.config['product_id']='Unknown'
        self.config['serial_num']=0
        self.config['vendor_id']='Unknown'
        self.config['local_mover'] = 0 #XXX who still looks at this?
        
        self.driver_type = self.config['driver']
        if self.driver_type == 'NullDriver':
            self.device = None
            import null_driver
            self.tape_driver = null_driver.NullDriver()
        elif self.driver_type == 'FTTDriver':
            self.device = self.config['device']
            import ftt_driver
            import ftt
            self.tape_driver = ftt_driver.FTTDriver()
            self.tape_driver.open(self.device, 0)
            stats = self.tape_driver.ftt.get_stats()
            self.config['product_id'] = stats[ftt.PRODUCT_ID]
            self.config['serial_num'] = stats[ftt.SERIAL_NUM]
            self.config['vendor_id'] = stats[ftt.VENDOR_ID]
            self.tape_driver.close()
            self.tape_driver.set_fast_rate(self.fast_rate)
            try: #see if there's a tape already loaded
                self.tape_driver.open(self.device, 0)
                self.tape_driver.set_mode(compression = 0, blocksize = 0)
                self.tape_driver.rewind()
                buf=80*' '
                self.tape_driver.read(buf, 0, 80)
                ##self.tape_driver.close()
                buf = string.split(buf)[0]
                if buf[:4]=='VOL1':
                    volname=buf[4:]
                    self.current_volume = volname
                    if verbose: print "have vol %s at startup" % (self.current_volume,)
            except (e_errors.READ_ERROR, ftt.FTTError), detail:
                if verbose:
                    print "checking for loaded tape:", detail
                try:
                    self.tape_driver.close()
                except:
                    pass
        else:
            print "Sorry, only Null and FTT driver allowed at this time"
            sys.exit(-1)
            
        #pass our verbosity level to the driver object
        self.tape_driver.verbose = verbose
        
	dispatching_worker.DispatchingWorker.__init__( self, self.address)
                
        self.libraries = []
        lib_list = self.config['library']

        if type(lib_list) != type([]):
            lib_list = [lib_list]
            
        for lib in lib_list:
            lib_config = self.csc.get(lib)
            self.libraries.append((lib, (lib_config['hostip'], lib_config['port'])))

        self.set_interval_func(self.update, 5) #this sets the period for messages to LM.
        self.set_error_handler(self.handle_mover_error)
        ##end of __init__
        
        
    def handle_mover_error(self, exc, msg, tb):
        if verbose: print "handle mover error", exc, msg
        print self.current_work_ticket, state_name(self.state)
        if self.current_work_ticket:
            try:
                if verbose: print "handle error: calling transfer failed",
                if verbose: print "str(msg)=", str(msg)
                self.transfer_failed((exc, msg))
            except:
                pass
                

    def update(self, reset_timer=None):
        if verbose:
            print "update"
            
        if not hasattr(self,'_last_state'):
            self._last_state = None

        if self.state in (CLEANING, DRAINING, OFFLINE):
            ### XXX when going offline, we still need to send a message to LM
            return

        ## See if the delayed dismount timer has expired
        now = time.time()
        if self.state is HAVE_BOUND and self.dismount_time and now>self.dismount_time:
            if verbose: print "Dismount time expired", self.current_volume
            self.dismount_volume()
            self.dismount_time = None
            self.state = IDLE
            self.clear_volume_status()
            self.mode = None
            
        ticket = self.format_lm_ticket()
        
        for lib, addr in self.libraries:
            if verbose and self.state != self._last_state:
                print "Send", ticket, "to", addr
            self.udpc.send_no_wait(ticket, addr)
        self._last_state=self.state
        if reset_timer:
            self.reset_interval_timer()
            
    def nowork( self, ticket ):
	return {}

    #I/O callbacks
    #################################
    def read_client(self, driver):
        if verbose>1:
            print "read client, buffer=", self.buffer.nbytes()
            print "bytes read:", self.bytes_read, "bytes to transfer", self.bytes_to_transfer
        while self.bytes_read < self.bytes_to_transfer and not self.buffer.full():

            nbytes = min(self.bytes_to_transfer - self.bytes_read, self.buffer.blocksize)
            bytes_read = self.buffer.stream_read(nbytes, driver)
            if verbose>1: print "read", bytes_read, "from client"
            if not bytes_read:  #  The client went away!
                self.transfer_failed(None)
                return
            self.bytes_read = self.bytes_read + bytes_read
            if not driver.ready_to_read():
                if verbose>1: print "net driver not ready to read"
                break #do not block 
        if self.bytes_written != self.bytes_to_transfer and (
            not self.buffer.low() or self.bytes_read==self.bytes_to_transfer):
            if verbose>1: print "enabling write tape"
            self.add_select_fd(self.tape_driver, WRITE, self.write_tape)
                        
    def write_tape(self, driver):
        if verbose>1: print "write tape, buffer =", self.buffer.nbytes()
        ##Dynamic setting of low-water mark
        if self.bytes_read >= self.buffer.min_bytes:
            netrate, junk = self.net_driver.rates()
            taperate, junk = self.tape_driver.rates()
            if taperate > 0:
                ratio = netrate/(taperate*1.0)
                optimal_buf = self.bytes_to_transfer * (1-ratio)
                optimal_buf = min(optimal_buf, self.max_buffer)
                optimal_buf = max(optimal_buf, 2*self.buffer.blocksize)
                if verbose:
                    print "netrate = %.3g, taperate=%.3g" % (netrate, taperate)
                    print "Changing buffer size from", self.buffer.min_bytes, "to", optimal_buf
                self.buffer.set_min_bytes(optimal_buf)
            
        if self.buffer.low() and self.bytes_read != self.bytes_to_transfer:
            # turn off the select fd, read_client will turn it back on
            if verbose>1: print "buffer low"
            self.remove_select_fd(driver)
            return
        while self.bytes_written<self.bytes_to_transfer: #keep pumping data to tape
            nbytes = min(self.bytes_to_transfer - self.bytes_written, self.buffer.blocksize)
            if verbose>1: print "write tape", nbytes
            if nbytes > self.buffer.nbytes():
                if verbose>1: print "only have", self.buffer.nbytes()
                self.remove_select_fd(driver)
                #not enough data in buffer to keep tape streaming
                return

            bytes_written = self.buffer.block_write(nbytes, driver)
            if verbose>1: print "wrote", nbytes, "to tape"
            if bytes_written != nbytes:
                self.transfer_failed(e_errors.WRITE_ERROR)
                break
            self.bytes_written = self.bytes_written + bytes_written
            if self.bytes_written == self.bytes_to_transfer:
                self.remove_select_fd(driver)
                self.tape_driver.writefm()
                print "FLUSH" #REMOVE
                self.tape_driver.flush()
                if self.update_after_writing():
                    self.transfer_completed()
                break
            if not driver.ready_to_write():
                if verbose>1: print "tape driver not ready to write"
                break # do not block
    ###################

    def read_tape(self, driver):
        if verbose>1:  print "read tape, buf=", self.buffer.nbytes()
        while self.bytes_read < self.bytes_to_transfer and not self.buffer.full():
            ##keep reading as long as the device has data for us 
            nbytes = min(self.bytes_to_transfer - self.bytes_read, self.buffer.blocksize)
            if self.bytes_read == 0 and nbytes<self.buffer.blocksize: #first read, try to read a whole block
                nbytes = self.buffer.blocksize

            bytes_read = self.buffer.block_read(nbytes, driver)
            if verbose>1: print "read", bytes_read, "from tape"
            self.bytes_read = self.bytes_read + bytes_read
            if self.bytes_read > self.bytes_to_transfer: #this is OK, we read a CPIO trailer or something
                self.bytes_read = self.bytes_to_transfer
            if not driver.ready_to_read():
                if verbose>1: print "tape driver not ready to read more data"
                break 
            
        if self.bytes_read == self.bytes_to_transfer or not self.buffer.low():
            if verbose>1: print "enabling write cli"
            self.add_select_fd(self.net_driver, WRITE, self.write_client)

    def write_client(self, driver):
        if verbose>1: print "write client, buf=", self.buffer.nbytes()
        if self.buffer.low() and self.bytes_read != self.bytes_to_transfer:
            self.remove_select_fd(driver) #turn off select fd, read_tape will turn it back on
            return
        while self.bytes_written < self.bytes_to_transfer and not self.buffer.empty():
            ###keep pumping data out to the client
            nbytes = min(self.bytes_to_transfer - self.bytes_written, self.buffer.blocksize)
            bytes_written = self.buffer.stream_write(nbytes, driver)
            if verbose>1: print "wrote", bytes_written, "to client"
            if bytes_written != nbytes:
                pass #this is not unexpected, since we send with MSG_DONTWAIT
            if bytes_written == -1: #this on the other hand is an error
                self.transfer_failed()
            self.bytes_written = self.bytes_written + bytes_written
            if self.bytes_written == self.bytes_to_transfer:
                self.transfer_completed()
                break
            if not driver.ready_to_write():
                if verbose>1: print "net driver not ready to write"
                break

    ########################################################################
    ########################################################################
        
    # the library manager has asked us to write a file to the hsm
    def write_to_hsm( self, ticket ):
        if verbose: print "WRITE TO HSM"
        self.current_work_ticket = ticket

        
        if not self.setup_transfer(ticket, mode=WRITE):
            return #XXX


        self.add_select_fd(self.net_driver, READ, self.read_client)
        self.state = ACTIVE        
        
    # the library manager has asked us to read a file to the hsm
    def read_from_hsm( self, ticket ):
        if verbose: print "READ FROM HSM"
        self.current_work_ticket = ticket
        
        if not self.setup_transfer(ticket, mode=READ):
            return #XXX

        self.add_select_fd( self.tape_driver, READ, self.read_tape)
        self.state = ACTIVE

    def setup_transfer(self, ticket, mode):
        if verbose: print "SETUP TRANSFER"
        if self.state not in (IDLE, HAVE_BOUND):
            if verbose: print "Not idle"
            Trace.trace(e_errors.ERROR, "Mover not idle: %s" %(state_name(self.state)))
            self.return_work_to_lm(ticket)
            return 0

        mode = {'read_from_hsm':READ, 'write_to_hsm': WRITE}.get(ticket['work'], None)

        if mode is None:
            if verbose: print "Huh?", ticket
            return 0

        if verbose: pprint.pprint(ticket)
        self.reset()

        self.current_work_ticket = ticket
        ##if not ticket.has_key('mover'): ## XXX cgw ask Sasha about this
        ticket['mover']={}
        ticket['mover'].update(self.config)
        ticket['mover']['device'] = "%s:%s" % (self.config['host'], self.config['device'])
        self.current_work_ticket = ticket
        self.control_socket, self.client_socket = self.connect_client()

        if verbose: print "client connect", self.control_socket, self.client_socket
        if not self.client_socket:
            ##XXX Log this
            return 0

        if self.config['driver'] == "NullDriver": # better way of keeping track of this?
            filename = ticket['wrapper'].get("pnfsFilename",'')
            if "NULL" not in string.split(filename,'/'):
                ticket['status']=(e_errors.USERERROR, "NULL not in PNFS path")
                self.send_client_done( ticket, e_errors.USERERROR, "NULL not in PNFS path" )
                return 0

        self.t0 = time.time()
        ##all groveling around in the ticket should be done here
        
        fc = ticket['fc']
        vc = ticket['vc']
        
        if verbose: print "vc=", vc

        self.vol_info.update(vc) ###XXX hack to get wrapper type - does this make sense?
        
        self.volume_family=vc['volume_family']

        delay = 0
        if ticket['encp'].has_key('delayed_dismount'):
            delay = 60 * int(ticket['encp']['delayed_dismount']) #XXX is this right? minutes?
                                                                  ##what does the flag really mean?
            
        self.delay = max(delay, self.default_dismount_delay)
        if verbose: print "delay", self.delay
        self.fcc = file_clerk_client.FileClient( self.csc, bfid=0,
                                                 server_addr=fc['address'] )
        self.vcc = volume_clerk_client.VolumeClerkClient(self.csc,
                                                         server_addr=vc['address'])
        label = fc['external_label']

        if mode is WRITE:
            location = None
        else:
            location = fc['location_cookie']

        if not self.prepare_volume(label, mode, location):
            return 0

        ##vol_info got set as a side-effect of prepare_volume

        self.buffer.set_blocksize(self.vol_info['blocksize'])

        self.wrapper_type = self.vol_info.get('wrapper')
        if self.wrapper_type is None:
            ##XXX hack - why is wrapper not coming in on the vc ticket?
            ff = self.vol_info['file_family']
            self.wrapper_type = string.split(ff,'.')[-1]
            
        try:
            self.wrapper = __import__(self.wrapper_type + '_wrapper')
        except ImportError, detail:
            print detail
            self.wrapper = None
            
        self.client_filename = ticket['wrapper'].get('fullname','?')
        self.pnfs_filename = ticket['wrapper'].get('pnfsFilename', '?')

        self.mode = mode
        
        if self.mode == READ:
            self.files = (self.pnfs_filename, self.client_filename)
        elif self.mode == WRITE:
            self.files = (self.client_filename, self.pnfs_filename)
        
        self.bytes_to_transfer = long(fc['size'])
        
        return 1
        
    def transfer_failed(self, msg=None):
        if verbose: print "transfer aborted", msg
        ###XXX network errors should not count toward rd_err, wr_err
        if self.mode == WRITE:
            self.vcc.update_counts(wr_err=1, wr_access=1)
        else:
            self.vcc.update_counts(rd_err=1, rd_access=1)       
        msgstr = str(msg) #XXX convert to appropriate Enstore error
        self.transfers_failed = self.transfers_failed + 1
        self.timer('transfer_time')
        self.state = HAVE_BOUND
        self.remove_select_fd(self.net_driver)
        self.remove_select_fd(self.tape_driver)
        if msg:
            self.send_client_done(self.current_work_ticket, msgstr)
        self.net_driver.close()

        self.update(reset_timer=1)
        
    def transfer_completed(self):
        if verbose: print "transfer complete"
        if self.mode == WRITE:
            self.vcc.update_counts(self.current_volume, wr_access=1)
        else:
            self.vcc.update_counts(self.current_volume, rd_access=1)
        self.transfers_completed = self.transfers_completed + 1
        self.timer('transfer_time')
        self.state = HAVE_BOUND

        self.remove_select_fd(self.net_driver)
        self.remove_select_fd(self.tape_driver)

        ##self.tape_driver.flush() # moved to write_tape REMOVE
        self.net_driver.close()

        self.current_location = self.tape_driver.tell()

        if verbose: print "CGW: current_location = ", self.current_location
        now = time.time()

        if verbose: print "delay=", self.delay
        self.dismount_time = now + self.delay

        self.send_client_done(self.current_work_ticket, e_errors.OK)

        self.update(reset_timer=1)


    def update_after_writing(self):

        self.current_location = self.tape_driver.tell()
        remaining = self.vol_info['remaining_bytes']-self.bytes_written

        if self.driver_type == 'FTTDriver':
            import ftt
            stats = self.tape_driver.ftt.get_stats()
            if verbose:
                print 'stats[REMAIN_TAPE] = %s' % stats[ftt.REMAIN_TAPE]
            if stats[ftt.REMAIN_TAPE]:
                remaining = stats[ftt.REMAIN_TAPE] * 1024L

        if verbose: print "current location: %s type %s, remain_tape=%s" % (
            self.current_location, type(self.current_location), remaining)

        eod = '%012d'%self.current_location
        self.vol_info['eod_cookie'] = eod
        self.vol_info['remaining_bytes']=remaining


        if verbose: print "CGW: last seek = %s, current_location = %s, eod = %s"%(
            self.last_seek,  self.current_location, eod)

        fc_ticket = {'location_cookie':'%012d'%(self.last_seek),
                     'size': self.bytes_to_transfer,
                     'sanity_cookie': (0,0L),
                     'external_label': self.current_volume,
                     'complete_crc': 0L}

        fcc_reply = self.fcc.new_bit_file( {'work':"new_bit_file",
                                            'fc'  : fc_ticket
                                            } )
        if fcc_reply['status'][0] != e_errors.OK:
            Trace.log( e_errors.ERROR,
                       "cannot assign new bfid")

            self.transfer_failed((e_errors.ERROR,"Cannot assign new bit file ID"))
            #XXX exception?
            return 0

        bfid = fcc_reply['fc']['bfid']
        self.current_work_ticket['fc'] = fcc_reply['fc']

        if verbose: print "set remaining: ", self.current_volume, remaining, eod
        reply = self.vcc.set_remaining_bytes( self.current_volume,
                                              remaining, eod,
                                              bfid )
        if verbose: print "set remaining returns", reply
        self.vol_info.update(reply)

        vol_info = self.query_volume_clerk(self.current_volume)
        self.vol_info.update(vol_info)
        self.update_volume_status(self.vol_info)
        return 1
        
    def reset(self):
        self.current_work_ticket = None
        self.buffer.reset()
        self.bytes_read = 0L
        self.bytes_written = 0L

        
    def return_work_to_lm(self,ticket):
        try:
            lm_address = ticket['lm']['address']
        except KeyError, msg:
            self.malformed_ticket(ticket, "[lm][address]")
            return
        
        ticket = self.format_lm_ticket(state=ERROR, error_info=(e_errors.MOVER_BUSY, ticket))
        self.udpc.send_no_wait(ticket, lm_address)


    def query_volume_clerk(self, label): ###XXX is this function needed or should we just use vcc.
        if verbose: print "doing inquire_volume"
        vol_info=self.vcc.inquire_vol(label)
        ##XXX side-effect, yuk
        self.vol_info.update(vol_info)
        return vol_info 
    
    def update_volume_status(self, vol_info):
        self.volume_status = (vol_info.get('system_inhibit',['Unknown', 'Unknown']),
                           vol_info.get('user_inhibit',['Unknown', 'Unknown']))

    def clear_volume_status(self):
        self.volume_status = (['none', 'none'], ['none','none'])
        self.vol_info = {}
        
    def prepare_volume(self, volume_label, iomode, location=None):
        if verbose: print "prepare", volume_label, iomode, location
        if iomode is READ and location is None:
            if verbose: print "prepare_volume: no location"
            return 0
        
        vol_info = self.query_volume_clerk(volume_label)
        if vol_info['status'][0] != 'ok': ###XXX I hate this kind of check
            return 0 #NOTAPE
        
        if verbose: print "self.vol_info =", self.vol_info

        if self.current_volume != volume_label:
            if self.current_volume: #XXX or unconditional?
                self.dismount_volume()

        self.mount_volume(volume_label)
        
        print "Opening tape driver"
        self.tape_driver.open(self.device, iomode)
        self.tape_driver.set_mode(compression = 0, blocksize = 0)            
        print "tape driver", self.tape_driver.fileno()

        if iomode is WRITE:
            status = self.vcc.set_writing(volume_label)
            if status['status'][0] != "ok":
                self.transfer_failed(e_errors.WRITE_NOTAPE)
                return 0
            eod = vol_info['eod_cookie']
            if eod in (None, "none"):
                ## new tape, label it
                self.tape_driver.rewind()
                vol1_label = 'VOL1'+ volume_label
                vol1_label = vol1_label+ (79-len(vol1_label))*' ' + '0'
                if verbose: print "labeling new tape", vol1_label
                self.tape_driver.write(vol1_label, 0, 80)
                self.tape_driver.writefm()
                eod = 1
                vol_info['eod_cookie'] = eod
                if self.driver_type == 'FTTDriver':
                    import ftt
                    stats = self.tape_driver.ftt.get_stats()
                    rt = stats[ftt.REMAIN_TAPE]
                    if rt is not None:
                        vol_info['remaining_bytes'] = rt * 1024L #XXX keep everything in KB?
                self.vcc.set_remaining_bytes( volume_label,
                                              vol_info['remaining_bytes'],
                                              vol_info['eod_cookie'])

            if location is None:
                location = eod
            if location != eod:
                return 0# Can only write at end of tape

            
        self.seek_to_location(location)
        self.last_seek = self.current_location
        return 1
    
    def malformed_ticket(self, ticket, expected_keys=None):
        msg = "Missing keys "
        if expected_keys is not None:
            msg = "%s %s"(msg, expected_keys)
        msg = "%s %s"%(msg, ticket)
        if verbose: print msg
        Trace.log(e_errors.ERROR, msg)

    def send_client_done( self, ticket, status, error_info=None):
        ticket['status'] = (status, error_info)
        callback.write_tcp_obj( self.control_socket, ticket)
        self.control_socket.close()
        self.control_socket = None
        return
    

    def format_lm_ticket(self, state=None, error_info=None):

        status = e_errors.OK, None

        if state is None:
            state = self.state

        if state is IDLE:
            work = "mover_idle"
        elif state in (MOUNT_WAIT, HAVE_BOUND):
            work = "mover_bound_volume"
        elif state in (ACTIVE, DISMOUNT_WAIT):
            work = "mover_busy"
        elif state is ERROR:
            work = "mover_error"
            if error_info is None:
                status = self.last_error
            else:
                status = error_info

        ticket =  {
            "mover":  self.name,
            "address": self.address,
            "external_label":  self.current_volume,
            "current_location": "%012d"%self.current_location,
            "status": status, 
            "volume_family": self.volume_family,
            "volume_status": self.volume_status,
            "operation": mode_name(self.mode),
            "work": work,
            }
        return ticket


    def dismount_volume(self):
        if not self.current_volume:
            if verbose: print "Precautionary dismount"
        if verbose: print "dismounting", self.current_volume
        self.last_volume = self.current_volume
        self.last_location = self.current_location
        self.current_volume = None
        self.tape_driver.close() 
        return

    def mount_volume(self, volume_label):
        if verbose: print "mounting", volume_label
        self.timer('mount_time')
        self.current_volume = volume_label
        return
    
    def seek_to_location(self, location):
        if verbose: print "seek to", location
        self.tape_driver.seek(location)
        self.timer('seek_time')
        self.current_location = self.tape_driver.tell()
        
    # data transfer takes place on tcp sockets, so get ports & call client
    # Info is added to ticket
    def connect_client(self):
        if verbose: print "connect client"
        try:
            ticket = self.current_work_ticket
            data_ip=self.config.get("data_ip",None)
            host, port, listen_socket = callback.get_data_callback(fixed_ip=data_ip)
            listen_socket.listen(4)
            ticket['mover']['callback_addr'] = (host,port) #client expects this
            # ticket must have 'callback_addr' set for the following to work
            control_socket = callback.user_callback_socket( ticket)
            if verbose: print "ctrl = ", control_socket
            # we expect a prompt call-back here
            
            read_fds,write_fds,exc_fds=select.select(
                [listen_socket],[],[],30) # half minute timeout
            if listen_socket in read_fds:
                client_socket, address = listen_socket.accept()
                listen_socket.close()
                self.net_driver.fdopen(client_socket)
                return control_socket, client_socket
            else:
                if verbose: print "timeout on waiting for client connect"
                return None, None
            
        except:
            exc, msg, tb = sys.exc_info()
            print exc, msg
            traceback.print_tb(tb)
            return None, None #XXX

    
    def status( self, ticket ):

	tick = { 'status'       : (e_errors.OK,None),
		 'drive_sn'     : self.config['serial_num'],
                 'drive_vendor' : self.config['vendor_id'],
                 'drive_id'     : self.config['product_id'],
		 #
		 'crc_flag'     : str(self.crc_flag),
		 'state'        : state_name(self.state),
		 'transfers_completed'     : self.transfers_completed,
                 'transfers_failed': self.transfers_failed,
		 'bytes_read'     : self.bytes_read,
		 'bytes_written'     : self.bytes_written,
                 'bytes_buffered' : self.buffer.nbytes(),
		 # from "work ticket"
		 'bytes_to_transfer': self.bytes_to_transfer,
		 'files'        : self.files,
		 'mode'         : mode_name(self.mode),
                 'current_volume': self.current_volume,
		 'current_location': self.current_location,
		 'last_volume' : self.last_volume,
		 'last_location': self.last_location,
		 'time_stamp'   : time.time(),
                 }

	self.reply_to_caller( tick )
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
        if verbose: print self.current_work_ticket['times']


    
    def lockfile_name(self):
        d=os.environ.get("ENSTORE_TMP","/tmp")
        return os.path.join(d, "mover_lock")
        
    def create_lockfile(self):
        filename=self.lockfile_name()
        try:
            f=open(filename,'w')
            f.write('locked\n')
            f.close()
        except IOError:
            Trace.log(e_errors.ERROR, "Cannot write %s"%(filename,))
            
    def remove_lockfile(self):
        filename=self.lockfile_name()
        try:
            os.unlink(filename)
        except IOError:
            Trace.log(e_errors.ERROR, "Cannot unlink %s"%(filename,))

    def check_lockfile(self):
        return os.path.exists(self.lockfile_name())
        
    def start_draining(self, ticket):	    # put itself into draining state
        self.state = OFFLINE
        self.create_lockfile()
	out_ticket = {'status':(e_errors.OK,None)}
	self.reply_to_caller( out_ticket )
	return

    def stop_draining(self, ticket):	    # put itself into draining state
        if self.state != OFFLINE:
            out_ticket = {'status':("EPROTO","Not in draining state")}
            self.reply_to_caller( out_ticket )
            return
        self.state = IDLE
        out_ticket = {'status':(e_errors.OK,None)}
        self.reply_to_caller( out_ticket )
        self.remove_lockfile()

        
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

#############################################################################

#############################################################################

if __name__ == '__main__':            

    if len(sys.argv)<2:
        sys.argv=["python", "m2.mover"] #REMOVE cgw
    # get an interface, and parse the user input

    intf = MoverInterface()
    mover =  Mover( (intf.config_host, intf.config_port), intf.name )
    while 1:
        try:
            mover.serve_forever()
        except:
            ### raise #REMOVE
            try:
                exc, msg, tb = sys.exc_info()
                print exc, msg, "restarting"
            except:
                pass
            
    Trace.log(e_errors.INFO, 'ERROR returned from serve_forever')
    



