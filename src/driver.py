import sys
from errno import *
import pprint
import posix
try:
    import ETape
except:
    print "ETape unavailable!"


class GenericDriver:

    def __init__(self, device, eod_cookie,remaining_bytes):
        self.device = device
        self.remaining_bytes = remaining_bytes
           # When a volume is ceated, the system sets EOD cookie to "none"
        if eod_cookie == "none" :
            self.eod = 0
        else:
            self.eod = eval(eod_cookie)
        self.wr_err = 0
        self.rd_err = 0
        self.wr_mnt = 0
        self.rd_mnt = 0

    def load(self):
        pass

    def unload(self):
        pass

    # blocksize is volume dependent
    def set_blocksize(self,blocksize):
        self.blocksize = blocksize

    def get_blocksize(self) :
        return self.blocksize

    def get_eod_remaining_bytes(self):
        return self.remaining_bytes

    def get_eod_cookie(self):
        return repr(self.eod)

    def get_errors(self) :
        return (self.wr_err, self.rd_err,\
                self.wr_mnt, self.rd_mnt)


class  FTTDriver(GenericDriver) :
    """
     A Fermi Tape Tools driver
    """

    def __init__(self, device, eod_cookie, remaining_bytes):
        GenericDriver.__init__(self, device, eod_cookie, remaining_bytes)
        self.blocksize = 65536
        self.set_position()

    # This may be a mixin where the position is determined from the drive
    def set_position(self):
        self.position = 0;

    def open_file_read(self, file_location_cookie) :
        loc = eval(file_location_cookie)
        move = loc - self.position
        if move < 0 :
           move = move-1
        print "loc",loc,self.position,move
        self.ETdesc = ETape.ET_OpenRead(self.device, move, self.blocksize)
        self.position = loc

    def close_file_read(self) :
        self.position = self.position + 1
        return ETape.ET_CloseRead(self.ETdesc)

    def read_block(self):
        x = ETape.ET_ReadBlock (self.ETdesc)
        return x

    def open_file_write(self):
        self.ETdesc = ETape.ET_OpenWrite(self.device, self.eod-self.position, self.blocksize)

    def close_file_write(self):
        errcnt = ETape.ET_CloseWrite(self.ETdesc)
        self.eod = self.eod + 1
        self.position = self.eod
        self.remaining_bytes = errcnt['Remain']
        return errcnt

    def write_block(self, data):
       ETape.ET_WriteBlock(self.ETdesc, data)

class  RawDiskDriver(GenericDriver) :
    """
    A driver for testing with disk files
    """

    def __init__(self, device, eod_cookie, remaining_bytes):
        GenericDriver.__init__(self, device, eod_cookie, remaining_bytes)
        self.df = open(device, "a+")
        self.set_eod(eod_cookie)
        self.blocksize = 4096

    def set_eod(self, eod_cookie) :
        # When a volume is ceated, the system sets EOD cookie to "none"
        if eod_cookie == "none" :
            self.eod = 0
        else:
            self.eod = eval(eod_cookie)

    # read file -- use the "cookie" to not walk off the end, since we have
    # no "file marks" on a disk
    def open_file_read(self, file_location_cookie) :
        self.firstbyte, self.pastbyte = eval(file_location_cookie)
        self.df.seek(self.firstbyte, 0)
        self.left_to_read = self.pastbyte - self.firstbyte

    def close_file_read(self) :
        pass

    def read_block(self):
        # no file marks on a disk, so use the information
        # in the cookie to bound the file.
        n_to_read = min(self.blocksize, self.left_to_read)
        if n_to_read == 0 : return ""
        buf = self.df.read(n_to_read)
        self.left_to_read = self.left_to_read - len(buf)
        if self.left_to_read < 0:
            raise "assert error"
        return buf

    def open_file_write(self):
        # we cannot auto sense a floppy, so we must trust the user
        self.df.seek(self.eod, 0)
        self.first_write_block = 1

    def close_file_write(self):
        first_byte = self.eod
        last_byte = self.df.tell()

        # we don't fill each byte - the next starting place is at the
        # beginning of the next block
        if last_byte%self.blocksize != 0:
            self.eod = last_byte+(self.blocksize-(last_byte%self.blocksize))

            # If the data is being written to a file on a hard drive, the
            # file has to be blanked filled to the next blocksize.
            # Otherwise, the next open_write doesn't seek to end
            empty = self.eod-last_byte    # number of empty bytes to next block
            self.first_write_block = 0    # just filling it it
            self.write_block("J"*empty)   # fill it out
        else:
            self.eod = last_byte

        return `(first_byte, last_byte)`  # cookie describing the file

    # write a block of data to already open file: user has to handle exceptions
    def write_block(self, data):
        if len(data) > self.remaining_bytes :
            raise errorcode[ENOSPC], NoSpace
        self.remaining_bytes = (self.remaining_bytes-len(data))
        self.df.write(data)
        self.df.flush()
        if self.first_write_block :
            self.first_write_block = 0
            self.eod = self.df.tell() - len(data)

if __name__ == "__main__" :
    import getopt
    import socket
    import string

    status = 0

    # defaults
    size = 760000
    device = "./rdd-testfile.fake"
    eod_cookie = "0"
    list = 0

    # see what the user has specified. bomb out if wrong options specified
    options = ["size=","device=","eod_cookie=","list","help"]
    optlist,args=getopt.getopt(sys.argv[1:],'',options)
    for (opt,value) in optlist :
        if opt == "--size" :
            size = string.atoi(value)
        elif opt == "--device" :
            device = value
        elif opt == "--eod_cookie" :
            eod_cookie = value
        elif opt == "--list" :
            list = 1
        elif opt == "--help" :
            print "python ",sys.argv[0], options
            print "   do not forget the '--' in front of each option"
            sys.exit(0)


    if list:
        print "Creating RawDiskDriver device",device, "with",size,"bytes"
    rdd = RawDiskDriver (device,eod_cookie,size)
    rdd.load()

    cookie = {}

    try:
        if list:
            print "writing 1 0's"
        rdd.open_file_write()
        rdd.write_block("0"*1)
        cookie[0] = rdd.close_file_write()
        if list:
            print "   ok",cookie[0]

        if list:
            print "writing 10 1's"
        rdd.open_file_write()
        rdd.write_block("1"*10)
        cookie[1] = rdd.close_file_write()
        if list:
            print "   ok",cookie[1]

        if list:
            print "writing 100 2's"
        rdd.open_file_write()
        rdd.write_block("2"*100)
        cookie[2] = rdd.close_file_write()
        if list:
            print "   ok",cookie[2]

        if list:
            print "writing 1,000 3's"
        rdd.open_file_write()
        rdd.write_block("3"*1000)
        cookie[3] = rdd.close_file_write()
        if list:
            print "   ok",cookie[3]

        if list:
            print "writing 10,000 4's"
        rdd.open_file_write()
        rdd.write_block("4"*10000)
        cookie[4] = rdd.close_file_write()
        if list:
            print "   ok",cookie[4]

        if list:
            print "writing 100,000 5's"
        rdd.open_file_write()
        rdd.write_block("5"*100000)
        cookie[5] = rdd.close_file_write()
        if list:
            print "   ok",cookie[5]

        if list:
            print "writing 1,000,000 6's"
        rdd.open_file_write()
        rdd.write_block("6"*1000000)
        cookie[6] = rdd.close_file_write()
        if list:
            print "   ok",cookie[6]

        if list:
            print "writing 1,000,000 7's"
        rdd.open_file_write()
        rdd.write_block("7"*1000000)
        cookie[7] = rdd.close_file_write()
        print "   ok",cookie[7]

    except:
        if list:
            print "ok, processed exception:"\
                  ,sys.exc_info()[0],sys.exc_info()[1]

    if list:
        print "EOD cookie:",rdd.get_eod_cookie()
        print "lower bound on bytes available:", rdd.get_eod_remaining_bytes()
        pprint.pprint(rdd.__dict__)

    for k in cookie.keys() :
        rdd.open_file_read(cookie[k])
        readback = rdd.read_block()
        rlen = len(readback)
        if rlen != 10**k and rlen != rdd.blocksize :
            print "Read error on cookie",k, cookie[k],"- not enough bytes. "\
                  +"Read=",rlen ," should have read= ",10**k
            status = status|1
        if list:
            print "cookie=",k," readback[0]=",readback[0]\
                  ,"readback[end]=",readback[rlen-1]
        if readback[0] != repr(k) or readback[rlen-1] != repr(k) :
            print "Read error. Should have read",k, " but "\
                  ,"First=",readback[0],"  Last=",readback[rlen-1]
            status = status|2
        rdd.close_file_read()

    rdd.unload()

    sys.exit(status)
