/* EXfer.c - Low level data transfer C modules for encp. */

/* $Id$*/


#ifndef STAND_ALONE
#include <Python.h>
#else
#define _GNU_SOURCE
#endif
#include <sys/stat.h>
#include <sys/types.h>
#include <stdio.h>
#include <unistd.h>
#include <errno.h>
#include <malloc.h>
#include <alloca.h>
#include <sys/time.h>
#include <signal.h>
#include <sys/socket.h>
#include <fcntl.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <pthread.h>
#include <sys/mman.h>
#include <stdarg.h>

/***************************************************************************
 constants and macros
**************************************************************************/

/* return/break - read error */
#define READ_ERROR (-1)
/* timeout - treat as an EOF */
#define TIMEOUT_ERROR (-2)
/* return a write error */
#define WRITE_ERROR (-3)

/*Number of buffer bins to use*/
/*#define ARRAY_SIZE 3*/
#define THREAD_ERROR (-4)
#ifdef __linix__
#define __USE_BSD
#endif

/* Define DEBUG only for extra debugging output */
/*#define DEBUG*/

/* Define PROFILE only for extra time output */
/* This profile only works for the non-threaded implementation.  It works
   best if the block size is changed on the encp command line to something
   small (i.e. less than the page size). */
/*#define PROFILE*/
#ifdef PROFILE
#define PROFILE_COUNT 25000
#endif

/* Macro to convert struct timeval into double. */
#define extract_time(t) ((double)(t->tv_sec+(t->tv_usec/1000000.0)))

/* Define memory mapped i/o advise constants on systems without them. */
#ifndef MADV_SEQUENTIAL
#define MADV_SEQUENTIAL -1
#endif
#ifndef MADV_WILLNEED
#define MADV_WILLNEED -1
#endif

/***************************************************************************
 definitions
**************************************************************************/

struct transfer
{
  int fd;                 /*file descriptor*/

  unsigned long long size;  /*size in bytes*/
  unsigned long long bytes; /*bytes left to transfer*/
  unsigned int block_size;  /*size of block*/
  int array_size;         /*number of buffers to use*/
  long mmap_size;         /*mmap address space segment lengths*/

  void *mmap_ptr;         /*memory mapped i/o pointer*/
  off_t mmap_len;         /*length of memory mapped file offset*/
  off_t mmap_offset;      /*Offset from beginning of mmapped segment. */
  off_t mmap_left;        /* Bytes to next mmap segment. */
  int mmap_count;         /* Number of mmapped segments done. */

  long long fsync_threshold; /* Number of bytes to wait between fsync()s. */
  long long last_fsync;      /* Number of bytes done though last fsync(). */

  struct timeval timeout; /*time to wait for data to be ready*/
  double transfer_time;   /*time spent transfering data*/

  int crc_flag;           /*crc flag - 0 or 1*/
  unsigned int crc_ui;    /*checksum*/
  
  int transfer_direction; /*positive means write, negative means read*/
  
  int direct_io;          /*is true if using direct io*/
  int mmap_io;            /*is true if using memory mapped io*/
  int threaded;           /*is true if using threaded implementation*/
  
  short int done;         /*is true if this part of the transfer is finished.*/
  
  int exit_status;        /*error status*/
  int errno_val;          /*errno of any errors (zero otherwise)*/
  char* msg;              /*additional error message*/
  int line;               /*line number where error occured*/
  char* filename;         /*filename where error occured*/

};

#ifdef PROFILE
struct profile
{
  char whereami;
  struct timeval time;
  int status;
  int error;
};
#endif


/***************************************************************************
 prototypes
**************************************************************************/

/*checksumming is now being done here, instead of calling another module,
  in order to save a strcpy  -  cgw 1990428 */
unsigned int adler32(unsigned int, char *, int);

#ifndef STAND_ALONE
void initEXfer(void);
static PyObject * raise_exception(char *msg);
static PyObject * EXfd_xfer(PyObject *self, PyObject *args);
#endif
void do_read_write_threaded(struct transfer *reads, struct transfer *writes);
void do_read_write(struct transfer *reads, struct transfer *writes);
static struct transfer* pack_return_values(struct transfer *info,
					   unsigned int crc_ui,
					   int errno_val, int exit_status,
					   char* msg,
					   double transfer_time,
					   char *filename, int line);
static double elapsed_time(struct timeval* start_time,
			   struct timeval* end_time);
static double rusage_elapsed_time(struct rusage *sru, struct rusage *eru);
static long long get_fsync_threshold(long long bytes, int blk_size);
static long align_to_page(long value);
static long align_to_size(long value, long align);
static int setup_mmap_io(struct transfer *info);
static int setup_direct_io(struct transfer *info);
static int setup_posix_io(struct transfer *info);
static int reinit_mmap_io(struct transfer *info);
static int finish_mmap(struct transfer *info);
static int finish_write(struct transfer *info);
static int do_select(struct transfer *info);
static ssize_t mmap_read(void *dst, size_t bytes_to_transfer,
			 struct transfer *info);
static ssize_t mmap_write(void *src, size_t bytes_to_transfer,
			  struct transfer *info);
static ssize_t posix_read(void *dst, size_t bytes_to_transfer,
			  struct transfer* info);
static ssize_t posix_write(void *src, size_t bytes_to_transfer,
			   struct transfer* info);
int thread_init(struct transfer *info);
int thread_wait(int bin, struct transfer *info);
int thread_signal(int bin, size_t bytes, struct transfer *info);
static void* thread_read(void *info);
static void* thread_write(void *info);
#ifdef PROFILE
void update_profile(int whereami, int sts, int sock,
		    struct profile *profile_data, long *profile_count);
void print_profile(struct profile *profile_data, int profile_count);
#endif /*PROFILE*/
#ifdef DEBUG
static void print_status(FILE *fp, int, int, struct transfer *info);
			 /*char, long long, unsigned int, int array_size);*/
#endif /*DEBUG*/

/***************************************************************************
 globals
**************************************************************************/

#ifndef STAND_ALONE

static PyObject *EXErrObject;

static char EXfer_Doc[] =  "EXfer is a module which Xfers data";

static char EXfd_xfer_Doc[] = "\
fd_xfer(fr_fd, to_fd, no_bytes, blk_siz, crc_flag[, crc])";

/*  Module Methods table. 

    There is one entry with four items for for each method in the module

    Entry 1 - the method name as used  in python
          2 - the c implementation function
	  3 - flags 
	  4 - method documentation string
	  */

static PyMethodDef EXfer_Methods[] = {
    { "fd_xfer",  EXfd_xfer,  1, EXfd_xfer_Doc},
    { 0, 0}        /* Sentinel */
};

#endif

int *stored;   /*pointer to array of bytes copied per bin*/
char *buffer;  /*pointer to array of buffer bins*/
pthread_mutex_t *buffer_lock; /*pointer to array of bin mutex locks*/
pthread_mutex_t done_mutex; /*used to signal main thread a thread returned*/
pthread_cond_t done_cond;   /*used to signal main thread a thread returned*/
pthread_cond_t next_cond;   /*used to signal peer thread to continue*/
#ifdef DEBUG
pthread_mutex_t print_lock; /*order debugging output*/
#endif

/***************************************************************************
 user defined functions
**************************************************************************/

/* Pack the arguments into a struct return_values. */
static struct transfer* pack_return_values(struct transfer* retval,
					   unsigned int crc_ui,
					   int errno_val,
					   int exit_status,
					   char* message,
					   double transfer_time,
					   char* filename, int line)
{
  retval->crc_ui = crc_ui;             /* Checksum */
  retval->errno_val = errno_val;       /* Errno value if error occured. */
  retval->exit_status = exit_status;   /* Exit status of the thread. */
  retval->msg = message;               /* Additional error message. */
  retval->transfer_time = transfer_time;
  retval->line = line;             
  retval->filename = filename;

  /* Putting the following here is just the lazy thing to do. */

  /* Do not bother with checking return values for errors.  Should the
     pthread_* functions fail at this point, there is notthing else to
     do but set the global flag and return. */
  pthread_mutex_lock(&done_mutex);
  /* For the threaded transfer, indicates to the other threads that this
     thread is almost done. */
  retval->done = 1;
  /* For this code to work this must be executed after setting retval->done
     to 1 above. */
  pthread_cond_signal(&done_cond);
  pthread_mutex_unlock(&done_mutex);

  return retval;
}

static double elapsed_time(struct timeval* start_time,
			   struct timeval* end_time)
{
  double elapsed_time;  /* variable to hold the time difference */

  elapsed_time = (extract_time(end_time) - extract_time(start_time));

  return elapsed_time;
}

/* Function to take two usage structs and return the total time difference. */
static double rusage_elapsed_time(struct rusage *sru, struct rusage *eru)
{
  return ((extract_time((&(eru->ru_stime)))+extract_time((&(eru->ru_utime)))) -
	  (extract_time((&(sru->ru_stime)))+extract_time((&(sru->ru_utime)))));
}

static long long get_fsync_threshold(long long bytes, int blk_size)
{
  long long temp_value;

  /* Find out what one percent of the file size is. */
  temp_value = (long long)(bytes / (double)100.0);

  /* Return the larger of the block size and 1 percent of the file size. */
  return (temp_value > blk_size) ? temp_value : blk_size;
}

/* A usefull function to round a value to the next full page. */
static long align_to_page(long value)
{
   return align_to_size(value, sysconf(_SC_PAGESIZE));
}

/* A usefull function to round a vlue to the next full required
   alignment size. */
static long align_to_size(long value, long align)
{
   return (value % align) ? (value + align - (value % align)) : value;
}

/* Return 0 for false, >1 for true, <1 for error. */
int is_empty(int bin)
{
  int rtn = 0; /*hold return value*/

  /* Determine if the lock for the buffer_lock bin, bin, is ready. */
  if(pthread_mutex_lock(&buffer_lock[bin]) != 0)
  {
    return -1; /* If we fail here, we are likely to see it again. */
  }
  if(stored[bin] == 0)
  {
    rtn = 1;
  }
  if(pthread_mutex_unlock(&buffer_lock[bin]) != 0)
  {
    return -1; /* If we fail here, we are likely to see it again. */
  }

  return rtn;
}

/*First argument is the number of arguments to follow.
  The rest are the arguments to find the min of.*/
unsigned long long min(int num, ...)
{
  va_list ap;
  int i;
  unsigned long long min_val = ULONG_MAX; /*Note: should be ULLONG_MAX */
  unsigned long long current;

  va_start(ap, num);

  for(i = 0; i < num; i++)
  {
    if((current = va_arg(ap, unsigned long long)) < min_val)
      min_val = current;
  }
  return min_val;
}

/***************************************************************************/
/***************************************************************************/

#ifdef DEBUG
static void print_status(FILE* fp, int bytes_transfered,
			 int bytes_remaining, struct transfer *info)
{
  int i;
  char debug_print;
  char direction;

  /* Print F if entire bin is transfered, P if bin partially transfered. */
  debug_print = (bytes_remaining) ? 'P' : 'F';
  /* Print W if write R if read. */
  direction = (info->transfer_direction > 0) ? 'W' : 'R';
  
  pthread_mutex_lock(&print_lock);

  fprintf(fp, "%c%c bytes: %15lld crc: %10u | ",
	  direction, debug_print, info->bytes, info->crc_ui);

  for(i = 0; i < info->array_size; i++)
  {
    fprintf(fp, " %6d", stored[i]);
  }
  fprintf(fp, "\n");

  pthread_mutex_unlock(&print_lock);

}
#endif /*DEBUG*/

#ifdef PROFILE
void update_profile(int whereami, int sts, int sock,
		    struct profile *profile_data, long *profile_count)
{
  int size_var = sizeof(int);
  struct stat file_info;

  if(*profile_count < PROFILE_COUNT)
  {
    profile_data[*profile_count].whereami = whereami;
    profile_data[*profile_count].status = sts;
    gettimeofday(&(profile_data[*profile_count].time), NULL);
    if(fstat(sock, &file_info) == 0)
    {
      if(S_ISSOCK(file_info.st_mode))
	getsockopt(sock, SOL_SOCKET, SO_ERROR,
		   &profile_data[*profile_count].error, &size_var); 
    }
    (*profile_count)++;
  }
}

void print_profile(struct profile *profile_data, int profile_count)
{
  int i;

  for(i = 0; i < profile_count; i++)
    printf("%4d: sec: %11ld usec: %9ld  size: %10d  error: %3d\n",
	   profile_data[i].whereami,
	   profile_data[i].time.tv_sec,
	   profile_data[i].time.tv_usec,
	   profile_data[i].status,
	   profile_data[i].error);
}
#endif /*PROFILE*/


/***************************************************************************/
/***************************************************************************/

static int setup_mmap_io(struct transfer *info)
{
  int fd = info->fd;            /* The file descriptor in question. */
  struct stat file_info;        /* Information about the file to write to. */
  long long bytes = info->size; /* Number of bytes to transfer. */
  off_t mmap_len =              /* Offset needs to be mulitple of pagesize */
    align_to_size(info->mmap_size, info->block_size); /* and blocksize. */
  int advise_holder;

  /* Determine the length of the memory mapped segment. */
  mmap_len = (bytes<mmap_len)?bytes:mmap_len;
  /* Make sure that the memory map length is set correctly.  Even if
     this file descriptor can not do memory mapped i/o, the other
     transfer thread might. */
  info->mmap_len = mmap_len;
  info->mmap_ptr = MAP_FAILED;
  info->mmap_left = info->mmap_len;

  /* If the user did not select memory mapped i/o do not use it. */
  if(!info->mmap_io)
  {
     return 0;
  }

  /* Determine if the file descriptor is a real file. */
  errno = 0;
  if(fstat(fd, &file_info))
  {
    pack_return_values(info, 0, errno, THREAD_ERROR, /*bytes,*/ "fstat failed",
		       0.0, __FILE__, __LINE__);
    return 1;
  }
  /* If the file is a local disk, use memory mapped i/o on it. */
  if(S_ISREG(file_info.st_mode))
  {

    if(info->transfer_direction > 0)  /* If true, it is a write. */
    {
      /* Set the size of the file. */
      errno = 0;
      if(ftruncate(fd, bytes) < 0)
      {
	pack_return_values(info, 0, errno, THREAD_ERROR, /*bytes,*/
			   "ftruncate failed", 0.0, __FILE__, __LINE__);
	return 1;
      }
    }

    /* Create the memory mapped file. info->mmap_ptr will equal the
       starting memory address on success; MAP_FAILED on error. */
    info->mmap_ptr = mmap(NULL, mmap_len, PROT_WRITE | PROT_READ,
			  MAP_SHARED, fd, 0);

    if(info->mmap_ptr != MAP_FAILED)
    {
      if(info->transfer_direction > 0) /* If true, it is a write to disk. */
	advise_holder = MADV_SEQUENTIAL;
      else
	advise_holder = MADV_SEQUENTIAL | MADV_WILLNEED;
      
      /* Advise the system on the memory mapped i/o usage pattern. */
      errno = 0;
      if(madvise(info->mmap_ptr, mmap_len, advise_holder) < 0)
      {
	/* glibc versions prior to 2.2.x don't support the madvise function.
	   If it is found not to be supported, don't worry.  Use the
	   default read/write method.  This error sets errno to ENOSYS. */
	/* IRIX does not support use of MADV_SEQUENTIAL.  This error sets
	   errno to EINVAL. */

	/* Clear the memory mapped information. */
	munmap(info->mmap_ptr, info->mmap_len);
	info->mmap_ptr = MAP_FAILED;
      }
    }
  
    /* If mmap() or madvise() failed, reset the file to its original size. */
    if(info->mmap_ptr == MAP_FAILED)
    {
      errno = 0;
      if(ftruncate(fd, file_info.st_size) < 0)
      {
	pack_return_values(info, 0, errno, THREAD_ERROR,
			   "ftruncate failed", 0.0, __FILE__, __LINE__);
	return 1;
      }
    }
  }

  return 0;
}

static int reinit_mmap_io(struct transfer *info)
{
  int advise_value = 0; /* Advise hints for madvise. */

  /* If the file is a local disk, use memory mapped i/o on it. 
     Only advance to the next mmap segment when the previous one is done. */
  if(info->mmap_ptr != MAP_FAILED && info->mmap_offset == info->mmap_len)
  {
    /* Unmap the current mapped memory segment. */
    errno = 0;
    if(munmap(info->mmap_ptr, info->mmap_len) < 0)
    {
      pack_return_values(info, 0, errno, READ_ERROR,
			 "munmap failed", 0.0, __FILE__, __LINE__);
      return 1;
    }

    /* Reset these values for the next segment. */
    info->mmap_len = (info->bytes<info->mmap_len)?info->bytes:info->mmap_len;
    info->mmap_offset = 0;
    info->mmap_count += 1;
    info->mmap_left = info->mmap_len;
    
    /* Create the memory mapped file. */
    errno = 0;
    if((info->mmap_ptr = mmap(NULL, info->mmap_len, PROT_WRITE | PROT_READ,
			      MAP_SHARED, info->fd,
			      info->mmap_count * info->mmap_len)) 
       == (caddr_t)-1)
    {
      pack_return_values(info, 0, errno, READ_ERROR,
			 "mmap failed", 0.0, __FILE__, __LINE__);
      return 1;
    }
    
    if(info->transfer_direction > 0) /*write*/
      advise_value |= MADV_SEQUENTIAL;
    else if(info->transfer_direction < 0)
      advise_value |= (MADV_SEQUENTIAL | MADV_WILLNEED);
    
    /* Advise the system on the memory mapped i/o usage pattern. */
    errno = 0;
    if(madvise(info->mmap_ptr, info->mmap_len, advise_value) < 0)
    {
      pack_return_values(info, 0, errno, WRITE_ERROR,
			 "madvise failed", 0.0, __FILE__, __LINE__);
      return 1;
    }
  }
  else if(info->mmap_offset == info->mmap_len)
  {
    /* Reset these values for the next segment. Even if this thread does
       not care about page allignment, the other thread might. */
    info->mmap_len = (info->bytes<info->mmap_len)?info->bytes:info->mmap_len;
    info->mmap_offset = 0;
    info->mmap_count += 1;
    info->mmap_left = info->mmap_len;
  }

  return 0;
}

static int finish_mmap(struct transfer *info)
{
  if(info->mmap_ptr != MAP_FAILED)
  {
    /* Unmap the final mapped memory segment. */
    errno = 0;
    if(munmap(info->mmap_ptr, info->mmap_len) < 0)
    {
      pack_return_values(info, 0, errno, READ_ERROR,
			 "munmap failed", 0.0, __FILE__, __LINE__);
      return 1;
    }
  }
  return 0;
}

static int finish_write(struct transfer *info)
{
  if(info->mmap_ptr != MAP_FAILED)
  {
    /* Tell OS to write out the data now. */
    errno = 0;
    if(msync(info->mmap_ptr, info->mmap_len, MS_SYNC) < 0)
    {
      pack_return_values(info, 0, errno, WRITE_ERROR,
			 "msync failed", 0.0, __FILE__, __LINE__);
      return 1;
    }
  }
  else
  {
    /* If the file descriptor supports fsync force the data to be flushed to
       disk.  This can obviously fail for things like fsync-ing sockets, thus
       any errors are ignored. */
    fsync(info->fd);
  }

  return 0;
}


static int setup_direct_io(struct transfer *info)
{
  struct stat file_info;  /* Information about the file to read/write from. */

  /* If direct io was specified, check if it may work. */
  if(info->direct_io)
  {
    /* Determine if the file descriptor supports fsync(). */
    if(fstat(info->fd, &file_info))
    {
      pack_return_values(info, 0, errno, READ_ERROR, "fstat failed", 0.0,
			 __FILE__, __LINE__);
      return 1;
    }
    /* Direct IO can only work on regular files.  Even if direct io is 
       turned on the filesystem still has to support it. */
    if(! S_ISREG(file_info.st_mode))
      info->direct_io = 0;
  }

  return 0;
}

static int setup_posix_io(struct transfer *info)
{
  struct stat file_info;  /* Information about the file to read/write from. */

  /* Determine if the file descriptor supports fsync(). */
  if(fstat(info->fd, &file_info))
  {
    pack_return_values(info, 0, errno, THREAD_ERROR, "fstat failed",
		       0.0, __FILE__, __LINE__);
    return 1;
  }

  if(S_ISREG(file_info.st_mode))
  {
    /* Get the number of bytes to transfer between fsync() calls. */
    info->fsync_threshold = get_fsync_threshold(info->size, info->block_size);
    /* Set the current number of bytes remaining since last fsync to
       the size of the file. */
    info->last_fsync = info->size;
  }
  else
  {
    /* Get the number of bytes to transfer between fsync() calls. */
    info->fsync_threshold = 0;
    /* Set the current number of bytes remaining since last fsync to
       the size of the file. */
    info->last_fsync = 0;
  }

  return 0;
}

/***************************************************************************/
/***************************************************************************/

/* Handle waiting for the file descriptor. Return non-zero on error and
   zero on success. */
static int do_select(struct transfer *info)
{
  fd_set fds;                   /* For use with select(2). */
  struct timeval timeout;       /* Time to wait for data. */
  int sts = 0;                  /* Return value from various C system calls. */

  /* Initialize select values. */
  errno = 0;
  FD_ZERO(&fds);
  FD_SET(info->fd,&fds);
  timeout.tv_sec = info->timeout.tv_sec;
  timeout.tv_usec = info->timeout.tv_usec;
  
  /* Wait for there to be data on the descriptor ready for reading. */
  if(info->transfer_direction > 0)  /*write*/
    sts = select(info->fd+1, NULL, &fds, NULL, &timeout);
  else if(info->transfer_direction < 0)  /*read*/
    sts = select(info->fd+1, &fds, NULL, NULL, &timeout);
  if (sts < 0)
  {
    pack_return_values(info, 0, errno, READ_ERROR,
		       "fd select error", 0.0, __FILE__, __LINE__);
    return 1;
  }
  if (sts == 0)
  {
    pack_return_values(info, 0, errno, TIMEOUT_ERROR,
		       "fd select timeout", 0.0, __FILE__, __LINE__);
    return 1;
  }
  return 0;
}


static ssize_t mmap_read(void *dst, size_t bytes_to_transfer,
			 struct transfer *info)
{
  memcpy(dst, (void*)((unsigned int)info->mmap_ptr + 
		      (unsigned int)info->mmap_offset),
	 bytes_to_transfer);
  
 return bytes_to_transfer;
}

static ssize_t mmap_write(void *src, size_t bytes_to_transfer,
			  struct transfer *info)
{
  int sync_type = 0;            /* Type of msync() to perform. */

  /* If file supports memory mapped i/o. */
  errno = 0;
  memcpy((void*)((unsigned int)info->mmap_ptr +
		 (unsigned int)info->mmap_offset),
	 src,
	 bytes_to_transfer);

  /* If this is the very end of the file, don't just set the dirty pages
     to be written to disk, wait for them to be written out to disk. */
  if((info->bytes - bytes_to_transfer) == 0)
    sync_type = MS_SYNC;
  else
    sync_type = MS_ASYNC;

  /* Schedule the data for sync to disk now. */
  msync((void*)((unsigned int)info->mmap_ptr +
		(unsigned int)info->mmap_offset),
	bytes_to_transfer, sync_type);
  
  return bytes_to_transfer;
}

/* Act like the posix read() call.  But return all interpreted errors with -1.
   Also, set error values appropratly when detected. */
static ssize_t posix_read(void *dst, size_t bytes_to_transfer,
			  struct transfer* info)
{
  int sts = 0;                  /* Return value from various C system calls. */
  
  /* If direct io was specified, make sure the location is page aligned. */
  if(info->direct_io)
  {
    bytes_to_transfer = align_to_page(bytes_to_transfer);
  }

  errno = 0;
  sts = read(info->fd, dst, bytes_to_transfer);
  
  if (sts < 0)
  {
    pack_return_values(info, 0, errno, READ_ERROR,
		       "fd read error", 0.0, __FILE__, __LINE__);
    return -1;
  }
  if (sts == 0)
  {
    pack_return_values(info, 0, errno, TIMEOUT_ERROR,
		       "fd timeout", 0.0, __FILE__, __LINE__);
    return -1;
  }
  return sts;
}

static ssize_t posix_write(void *src, size_t bytes_to_transfer,
			   struct transfer* info)
{
  int sts = 0;                  /* Return value from various C system calls. */

  /* If direct io was specified, make sure the location is page aligned. */
  if(info->direct_io)
  {
    bytes_to_transfer = align_to_page(bytes_to_transfer);
  }

  /* When faster methods will not work, use read()/write(). */
  errno = 0;
  sts = write(info->fd, src, bytes_to_transfer);
  
  if (sts == -1)
  {
    pack_return_values(info, 0, errno, WRITE_ERROR,
		       "fd write error", 0.0, __FILE__, __LINE__);
    return -1;
  }
  if (sts == 0)
  {
    pack_return_values(info, 0, errno, TIMEOUT_ERROR,
		       "fd timeout", 0.0, __FILE__, __LINE__);
    return -1;
  }
  
  /* Use with direct io. */
  if(info->direct_io)
  {
    /* Only apply after the last write() call.  Also, if the size of the
       file was a multiple of the alignment used, then everything is correct
       and attempting to do this file size 'fix' is unnecessary. */
    if(info->bytes <= sts)
    {
      /* Adjust the sts. */
      sts = ((int)((signed long long)info->bytes));
      /* Truncate size at end of transfer.  For direct io all writes must be
	 a multiple of the page size.  The last write must be truncated down
	 to the correct size. */
      ftruncate(info->fd, info->size);
    }
  }
  else
  {
    /* Force the data to disk.  Don't let encp take up to much memory.
       This isnt the most accurate way of doing this, however it is less
       overhead. */
    if(info->fsync_threshold)
    {
      /* If the amount of data transfered between fsync()s has passed,
	 do the fsync and record amount completed. */
      if((info->last_fsync - info->bytes - sts) > info->fsync_threshold)
      {
	info->last_fsync = info->bytes - sts;
	fsync(info->fd);
      }
      /* If the entire file is transfered, do the fsync(). */
      else if((info->bytes - sts) == 0)
      {
	info->last_fsync = info->bytes - sts;
	fsync(info->fd);
      }
    }
  }

  return sts;
}

/***************************************************************************/
/***************************************************************************/

int thread_init(struct transfer *info)
{
  int p_rtn;                    /* Pthread return value. */
  int i;

  /* Initalize all the condition varaibles and mutex locks. */

  /* initalize the conditional variable signaled when a thread has finished. */
  if((p_rtn = pthread_cond_init(&done_cond, NULL)) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "cond init failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
  /* initalize the conditional variable to signal peer thread to continue. */
  if((p_rtn = pthread_cond_init(&next_cond, NULL)) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "cond init failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
  /* initalize the mutex for signaling when a thread has finished. */
  if((p_rtn = pthread_mutex_init(&done_mutex, NULL)) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "mutex init failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
#ifdef DEBUG
  /* initalize the mutex for ordering debugging output. */
  if((p_rtn = pthread_mutex_init(&print_lock, NULL)) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "mutex init failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
#endif
  /* initalize the array of bin mutex locks. */
  for(i = 0; i < info->array_size; i++)
    if((p_rtn = pthread_mutex_init(&(buffer_lock[i]), NULL)) != 0)
    {
      pack_return_values(info, 0, p_rtn, THREAD_ERROR,
			 "mutex init failed", 0.0, __FILE__, __LINE__);
      return 1;
    }
  
  return 0;
}

/* The first parameter is the bin to wait on.  The second parameter should
   be zero if waiting for the bin to be empty, non zero if needs to contain
   data.  Last paramater is the transfer struct for this half of the 
   transfer. */
int thread_wait(int bin, struct transfer *info)
{
  int p_rtn;                    /* Pthread return value. */
  struct timeval cond_wait_tv;  /* Absolute time to wait for cond. variable. */
  struct timespec cond_wait_ts; /* Absolute time to wait for cond. variable. */
  int expected;
  
  if(info->transfer_direction > 0)  /*write*/
    expected = 1;
  else                              /*read*/
    expected = 0;

  /* Determine if the lock for the buffer_lock bin, bin, is ready. */
  if((p_rtn = pthread_mutex_lock(&buffer_lock[bin])) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "mutex lock failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
  /* If they don't match then wait. */
  if(!stored[bin] != !expected)
  {
    /* Determine the absolute time to wait in pthread_cond_timedwait(). */
    gettimeofday(&cond_wait_tv, NULL);
    cond_wait_ts.tv_sec = cond_wait_tv.tv_sec + info->timeout.tv_sec;
    cond_wait_ts.tv_nsec = cond_wait_tv.tv_usec * 1000;
    
    /* This bin still needs to be used by the other thread.  Put this thread
       to sleep until the other thread is done with it. */
    if((p_rtn = pthread_cond_timedwait(&next_cond, &buffer_lock[bin],
				       &cond_wait_ts)) != 0)
    {
      pack_return_values(info, 0, p_rtn, THREAD_ERROR,
			 "waiting for condition failed",
			 0.0, __FILE__, __LINE__);
      return 1;
    }
  }
  if((p_rtn = pthread_mutex_unlock(&buffer_lock[bin])) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "mutex unlock failed", 0.0,
		       __FILE__, __LINE__);
    return 1;
  }

  /* Determine if the main thread sent the signal to indicate the other
     thread exited early from an error. If this value is still non-zero/zero,
     then assume there was an error. */
  if(!stored[bin] != !expected)
  {
    pack_return_values(info, 0, ECANCELED, THREAD_ERROR,
		       "waiting for condition failed",
		       0.0, __FILE__, __LINE__);
    return 1;
  }
  
  return 0;
}

int thread_signal(int bin, size_t bytes, struct transfer *info)
{
  int p_rtn;                    /* Pthread return value. */

  /* Obtain the mutex lock for the specific buffer bin that is needed to
     clear the bin for writing. */
  if((p_rtn = pthread_mutex_lock(&buffer_lock[bin])) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "mutex lock failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
  
  /* Set the number of bytes in the buffer. After a write this is set
     to zero, and after a read it is set to the amount read. */
  stored[bin] = bytes;

  /* If other thread sleeping, wake it up. */
  if((p_rtn = pthread_cond_signal(&next_cond)) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "waiting for condition failed",
		       0.0, __FILE__, __LINE__);
    return 1;
  }
  /* Release the mutex lock for this bin. */
  if((p_rtn = pthread_mutex_unlock(&buffer_lock[bin])) != 0)
  {
    pack_return_values(info, 0, p_rtn, THREAD_ERROR,
		       "mutex unlock failed", 0.0, __FILE__, __LINE__);
    return 1;
  }
  
  return 0;
}

/***************************************************************************/
/***************************************************************************/

void do_read_write_threaded(struct transfer *reads, struct transfer *writes)
{
  int array_size = reads->array_size;  /* Number of buffer bins. */
  int block_size = reads->block_size;  /* Align the buffers size. */
  int i;                               /* Loop counting. */
  int p_rtn = 0;                       /* pthread* return values. */
  pthread_t read_tid, write_tid;       /* Thread id numbers. */
  struct timeval cond_wait_tv;  /* Absolute time to wait for cond. variable. */
  struct timespec cond_wait_ts; /* Absolute time to wait for cond. variable. */

  /* Do stuff to the file descriptors. */

  /* Detect (and setup if necessary) the use of memory mapped io. */
  if(setup_mmap_io(reads))
    return;
  if(setup_mmap_io(writes))
    return;
  /* Detect (and setup if necessary) the use of direct io. */
  if(setup_direct_io(reads))
    return;
  if(setup_direct_io(writes))
    return;
  /* Detect (and setup if necessary) the use of posix io. */
  if(setup_posix_io(reads))
    return;
  if(setup_posix_io(writes))
    return;

  /* Allocate and initialize the arrays */

  errno = 0;
  if((stored = calloc(array_size, sizeof(int))) ==  NULL)
  {
    pack_return_values(reads, 0, errno, THREAD_ERROR,
		       "calloc failed", 0.0, __FILE__, __LINE__);
    pack_return_values(writes, 0, errno, THREAD_ERROR,
		       "calloc failed", 0.0, __FILE__, __LINE__);
    return;
  }
  errno = 0;
  if((buffer_lock = calloc(array_size, sizeof(pthread_mutex_t))) == NULL)
  {
    pack_return_values(reads, 0, errno, THREAD_ERROR,
		       "calloc failed", 0.0, __FILE__, __LINE__);
    pack_return_values(writes, 0, errno, THREAD_ERROR,
		       "calloc failed", 0.0, __FILE__, __LINE__);
    return;
  }
  errno = 0;
  if((buffer = memalign(sysconf(_SC_PAGESIZE),
			array_size * align_to_page(block_size))) == NULL)
  {
    pack_return_values(reads, 0, errno, THREAD_ERROR,
		       "memalign failed", 0.0, __FILE__, __LINE__);
    pack_return_values(writes, 0, errno, THREAD_ERROR,
		       "memalign failed", 0.0, __FILE__, __LINE__);
    return;
  }

  if(thread_init(reads))
  {
    /* Since this error is for both reads and writes, copy it over to 
       the writes struct. */
    memcpy(writes, reads, sizeof(reads));
    return;
  }
  /*Snag this mutex before spawning the new threads.  Otherwise, there is
    the possibility that the new threads will finish before the main thread
    can get to the pthread_cond_wait() to detect the threads exiting.*/
  if((p_rtn = pthread_mutex_lock(&done_mutex)) != 0)
  {
    pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
		       "mutex lock failed", 0.0, __FILE__, __LINE__);
    pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
		       "mutex lock failed", 0.0, __FILE__, __LINE__);
    return;
  }
  /* get the threads going. */
  if((p_rtn = pthread_create(&write_tid, NULL, &thread_write, writes)) != 0)
  {
    pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
		       "write thread creation failed", 0.0, __FILE__,__LINE__);
    pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
		       "write thread creation failed", 0.0, __FILE__,__LINE__);
    return;
  }
  if((p_rtn = pthread_create(&read_tid, NULL, &thread_read, reads)) != 0)
  {
    pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
		       "read thread creation failed", 0.0, __FILE__, __LINE__);
    pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
		       "read thread creation failed", 0.0, __FILE__, __LINE__);
    return;
  }

  /* Determine the absolute time to wait in pthread_cond_timedwait(). */
  if(gettimeofday(&cond_wait_tv, NULL) < 0)
  {
    pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
		       "read thread creation failed", 0.0, __FILE__, __LINE__);
    pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
		       "read thread creation failed", 0.0, __FILE__, __LINE__);
    return;
  }
  cond_wait_ts.tv_sec = cond_wait_tv.tv_sec + (60 * 60 * 6); /*wait 6 hours*/
  cond_wait_ts.tv_nsec = cond_wait_tv.tv_usec * 1000;

  /*This screewy loop of code is used to detect if a thread has terminated.
     If an error occurs either thread could return in any order.  If
     pthread_join() could join with any thread returned this would not
     be so complicated.*/
  while(!reads->done || !writes->done)
  {
    /* wait until the condition variable is set and we have the mutex */
    /* Waiting indefinatly could be dangerous. */

    if((p_rtn = pthread_cond_timedwait(&done_cond, &done_mutex,
				       &cond_wait_ts)) != 0)
    {
      pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
			 "waiting for condition failed", 0.0,
			 __FILE__, __LINE__);
      pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
			 "waiting for condition failed", 0.0,
			 __FILE__, __LINE__);
      return;
    }

    if(reads->done > 0) /*true when thread_read ends*/
    {
      if((p_rtn = pthread_join(read_tid, (void**)NULL)) != 0)
      {
	pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
			   "joining with read thread failed",
			   0.0, __FILE__, __LINE__);
	pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
			   "joining with read thread failed",
			   0.0, __FILE__, __LINE__);
	return;
      }

      if(reads->exit_status)
      {
	fprintf(stderr,
		"Read thread exited with error(%d) '%s' from %s line %d.\n",
		reads->errno_val, strerror(reads->errno_val),
		reads->filename, reads->line);

	/* Signal the other thread there was an error. We need to lock the
	   mutex associated with the next bin to be used by the other thread.
	   Since, we don't know which one, get them all. */
	for(i = 0; i < array_size; i++)
	    pthread_mutex_trylock(&(buffer_lock[i]));
	pthread_cond_signal(&next_cond);
	for(i = 0; i < array_size; i++)
	    pthread_mutex_unlock(&(buffer_lock[i]));
      }
      reads->done = -1; /* Set to non-positive and non-zero value. */
    }
    if(writes->done > 0) /*true when thread_write ends*/
    {
      if((p_rtn = pthread_join(write_tid, (void**)NULL)) != 0)
      {
	pack_return_values(reads, 0, p_rtn, THREAD_ERROR,
			   "joining with write thread failed",
			   0.0, __FILE__, __LINE__);
	pack_return_values(writes, 0, p_rtn, THREAD_ERROR,
			   "joining with write thread failed",
			   0.0, __FILE__, __LINE__);
	return;
      }
      if(writes->exit_status)
      {
	fprintf(stderr,
		"Write thread exited with error(%d) '%s' from %s line %d.\n",
		writes->errno_val, strerror(writes->errno_val),
		writes->filename, writes->line);

	/* Signal the other thread there was an error. We need to lock the
	   mutex associated with the next bin to be used by the other thread.
	   Since, we don't know which one, get them all.*/
	for(i = 0; i < array_size; i++)
	  pthread_mutex_trylock(&(buffer_lock[i]));
	pthread_cond_signal(&next_cond);
	for(i = 0; i < array_size; i++)
	  pthread_mutex_unlock(&(buffer_lock[i]));
      }
      writes->done = -1; /* Set to non-positive and non-zero value. */
    }
  }
  pthread_mutex_unlock(&done_mutex);

  /* Print out an error message.  This information currently is not returned
     to encp.py. */
  if(reads->exit_status)
  {
    fprintf(stderr, "Low-level transfer failure: [Errno %d] %s: higher "
	    "encp levels will process this error and retry if possible\n",
	    reads->errno_val, strerror(reads->errno_val));
    fflush(stderr);
  }
  if(writes->exit_status)
  {
    fprintf(stderr, "Low-level transfer failure: [Errno %d] %s: higher "
	    "encp levels will process this error and retry if possible\n",
	    writes->errno_val, strerror(writes->errno_val));
    fflush(stderr);
  }

  /*free the address space, this should only be done here if an error occured*/
  if(reads->mmap_ptr != MAP_FAILED)
    munmap(reads->mmap_ptr, reads->mmap_len);
  if(writes->mmap_ptr != MAP_FAILED)
    munmap(writes->mmap_ptr, writes->mmap_len);
  
  /*free the dynamic memory*/
  free(stored);
  free(buffer);
  free(buffer_lock);

  return;
}

static void* thread_read(void *info)
{
  struct transfer *read_info = (struct transfer*)info; /* dereference */
  size_t bytes_remaining;       /* Number of bytes to move in one loop. */
  size_t bytes_transfered;      /* Bytes left to transfer in a sub loop. */
  int sts = 0;                  /* Return value from various C system calls. */
  int bin = 0;                  /* The current bin (bucket) to use. */
  unsigned int crc_ui = 0;      /* Calculated checksum. */
  struct stat file_info;        /* Information about the file to read from. */
  struct timeval start_time;    /* Holds time measurement value. */
  struct timeval end_time;      /* Holds time measurement value. */
  struct rusage start_usage;    /* Hold time info from os billing. */
  struct rusage end_usage;      /* Hold time info from os billing. */
  struct timeval start_total;   /* Hold overall time measurment value. */
  struct timeval end_total;     /* Hold overall time measurment value. */
  double corrected_time = 0.0;  /* Corrected return time. */
  double transfer_time = 0.0;   /* Runing transfer time. */

  /* Initialize the time variables. */

  /* Initialize the running time incase of early failure. */
  memset(&start_time, 0, sizeof(struct timeval));
  memset(&end_time, 0, sizeof(struct timeval));
  /* Initialize the running time incase of early failure. */
  gettimeofday(&start_total, NULL);
  memcpy(&end_total, &start_total, sizeof(struct timeval));
  /* Initialize the thread's start time usage. */
  errno = 0;
  if(getrusage(RUSAGE_SELF, &start_usage))
  {
    pack_return_values(info, 0, errno, READ_ERROR, "getrusage failed", 0.0,
		       __FILE__, __LINE__);
    return NULL;
  }
  
  /* Determine if the file descriptor supports fsync(). */
  errno = 0;
  if(fstat(read_info->fd, &file_info))
  {
    pack_return_values(info, 0, errno, READ_ERROR, "fstat failed", 0.0,
		       __FILE__, __LINE__);
    return NULL;
  }

  while(read_info->bytes > 0)
  {
    /* If the mmapped memory segment is finished, get the next. */
    if(reinit_mmap_io(read_info))
      return NULL;

    /* If the other thread is slow, wait for it. */
    if(thread_wait(bin, read_info))
      return NULL;

    /* Determine the number of bytes to transfer during this inner loop. */
    bytes_remaining = min(3, (unsigned long long) read_info->bytes,
			  (unsigned long long) read_info->block_size,
			  (unsigned long long) read_info->mmap_left);
    /* Set this to zero. */
    bytes_transfered = 0;

    while(bytes_remaining > 0)
    {
      /* Record the time to start waiting for the read to occur. */
      gettimeofday(&start_time, NULL);
      
      /* Handle calling select to wait on the descriptor. */
      if(do_select(info))
	return NULL;
      
      /* Read in the data. */
      if(read_info->mmap_ptr != MAP_FAILED)
      {
	sts = mmap_read((buffer + (bin * read_info->block_size)),
			bytes_remaining, info);
      }
      else
      {
	/* Does double duty in that it also does the direct io read. */
	sts = posix_read(
	           (buffer + (bin * read_info->block_size) + bytes_transfered),
		   bytes_remaining, info);
	if(sts < 0)
	  return NULL;
      }

      /* Record the time the read operation completes. */
      gettimeofday(&end_time, NULL);
      /* Calculate wait time. */
      transfer_time += elapsed_time(&start_time, &end_time);

      /* Calculate the crc (if applicable). */
      switch (read_info->crc_flag)
      {
      case 0:  
	break;
      case 1:  
	crc_ui = adler32(crc_ui,
	     (buffer + (bin * read_info->block_size) + bytes_transfered), sts);
	read_info->crc_ui = crc_ui;
	break;
      default:  
	crc_ui = 0;
	read_info->crc_ui = crc_ui; 
	break;
      }

      /* Update this nested loop's counting variables. */
      bytes_remaining -= sts;
      bytes_transfered += sts;
      read_info->mmap_offset += sts;
      read_info->mmap_left -= sts;
      
#ifdef DEBUG
      print_status(stderr, bytes_transfered, bytes_remaining, read_info);
#endif /*DEBUG*/
    }

    if(thread_signal(bin, bytes_transfered, read_info))
       return NULL;

    /* Determine where to put the data. */
    bin = (bin + 1) % read_info->array_size;
    /* Determine the number of bytes left to transfer. */
    read_info->bytes -= bytes_transfered;
  }

  /* Sync the data to disk and other 'completion' steps. */
  if(finish_mmap(info))
    return NULL;

  /* Get total end time. */
  if(gettimeofday(&end_total, NULL))
  {
    pack_return_values(read_info, 0, errno, THREAD_ERROR,
		       "gettimeofday failed", 0.0, __FILE__, __LINE__);
    return NULL;
  }
  /* Get the thread's time usage. */
  errno = 0;
  if(getrusage(RUSAGE_SELF, &end_usage))
  {
    pack_return_values(read_info, 0, errno, THREAD_ERROR,
		       "getrusage failed", 0.0, __FILE__, __LINE__);
    return NULL;
  }

  /* If the descriptor is for a regular file returning the total time passed
     for use in the rate calculation appears accurate.  Unfortunatly, this
     method doesn't seem to return accurate time/rate information for sockets.
     Instead socket information seems most accurate by adding the total
     CPU time usage to the time spent in select() and read()/write(). */

  if(S_ISREG(file_info.st_mode))
    corrected_time = elapsed_time(&start_total, &end_total);
  else
    corrected_time = rusage_elapsed_time(&start_usage, &end_usage) +
      transfer_time;

  pack_return_values(info, read_info->crc_ui, 0, 0, "",
		     corrected_time, NULL, 0);
  return NULL;
}


static void* thread_write(void *info)
{
  struct transfer *write_info = (struct transfer*)info; /* dereference */
  size_t bytes_remaining;       /* Number of bytes to move in one loop. */
  size_t bytes_transfered;      /* Bytes left to transfer in a sub loop. */
  int sts = 0;                  /* Return value from various C system calls. */
  int bin = 0;                  /* The current bin (bucket) to use. */
  unsigned long crc_ui = 0;     /* Calculated checksum. */
  struct stat file_info;        /* Information about the file to write to. */
  struct timeval start_time;    /* Holds time measurement value. */
  struct timeval end_time;      /* Holds time measurement value. */
  struct rusage start_usage;    /* Hold time info from os billing. */
  struct rusage end_usage;      /* Hold time info from os billing. */
  struct timeval start_total;   /* Hold overall time measurment value. */
  struct timeval end_total;     /* Hold overall time measurment value. */
  double corrected_time = 0.0;  /* Corrected return time. */
  double transfer_time = 0.0;   /* Runing transfer time. */

  /* Initialize the time variables. */

  /* Initialize the running time incase of early failure. */
  memset(&start_time, 0, sizeof(struct timeval));
  memset(&end_time, 0, sizeof(struct timeval));
  /* Initialize the running time incase of early failure. */
  gettimeofday(&start_total, NULL);
  memcpy(&end_total, &start_total, sizeof(struct timeval));
  /* Get the thread's start time usage. */
  if(getrusage(RUSAGE_SELF, &start_usage))
  {
    pack_return_values(info, 0, errno, READ_ERROR, "getrusage failed", 0.0,
		       __FILE__, __LINE__);
    return NULL;
  }

  /* Get stat info. */
  errno = 0;
  if(fstat(write_info->fd, &file_info) < 0)
  {
    pack_return_values(info, 0, errno, WRITE_ERROR,
		       "fstat failed", 0.0, __FILE__, __LINE__);
    return NULL;
  }

  while(write_info->bytes > 0)
  {
    /* If the mmapped memory segment is finished, get the next. */
    if(reinit_mmap_io(info))
      return NULL;

    /* If the other thread is slow, wait for it. */
    if(thread_wait(bin, write_info))
      return NULL;

    /* Determine the number of bytes to transfer during this inner loop. */
    bytes_remaining = stored[bin];
    /* Set this to zero. */
    bytes_transfered = 0;

    while(bytes_remaining > 0)
    {
      /* Record the time to start waiting for the read to occur. */
      gettimeofday(&start_time, NULL);

      /* Handle calling select to wait on the descriptor. */
      if(do_select(info))
	return NULL;

      if(write_info->mmap_ptr != MAP_FAILED)
      {
	sts = mmap_write(
	          (buffer + (bin * write_info->block_size) + bytes_transfered),
		  bytes_remaining, info);
      }
      else
      {
	/* Does double duty in that it also does the direct io read. */
	sts = posix_write(
		  (buffer + (bin * write_info->block_size) + bytes_transfered),
			bytes_remaining, info);
	if(sts < 0)
	  return NULL;
      }

      /* Record the time that this thread wakes up from waiting for the
	 condition variable. */
      gettimeofday(&end_time, NULL);
      transfer_time += elapsed_time(&start_time, &end_time);

      /* Calculate the crc (if applicable). */
      switch (write_info->crc_flag)
      {
      case 0:
	break;
      case 1:
	crc_ui = adler32(crc_ui,
	     (buffer + (bin * write_info->block_size) + bytes_transfered),sts);
	/*to cause intentional crc errors, use the following line instead*/
	/*crc_ui=adler32(crc_ui, (buffer), sts);*/
	write_info->crc_ui = crc_ui;
	break;
      default:
	crc_ui=0;
	write_info->crc_ui = crc_ui;
	break;
      }

      /* Update this nested loop's counting variables. */
      bytes_remaining -= sts;
      bytes_transfered += sts;
      write_info->mmap_offset += sts;
      write_info->mmap_left -= sts;

#ifdef DEBUG
      print_status(stderr, bytes_transfered, bytes_remaining, write_info);
#endif /*DEBUG*/
    }

    if(thread_signal(bin, 0, write_info))
       return NULL;

    /* Determine where to get the data. */
    bin = (bin + 1) % write_info->array_size;
    /* Determine the number of bytes left to transfer. */
    write_info->bytes -= bytes_transfered;
  }

  /* If mmapped io was used, unmap the last segment. */
  if(finish_mmap(write_info))
    return NULL;

  /* Get total end time. */
  if(gettimeofday(&end_total, NULL))
  {
    pack_return_values(write_info, 0, errno, THREAD_ERROR,
		       "gettimeofday failed", 0.0, __FILE__, __LINE__);
    return NULL;
  }
  /* Get the thread's time usage. */
  errno = 0;
  if(getrusage(RUSAGE_SELF, &end_usage))
  {
    pack_return_values(write_info, 0, errno, THREAD_ERROR,
		       "getrusage failed", 0.0, __FILE__, __LINE__);
    return NULL;
  }

  /* If the descriptor is for a regular file returning the total time passed
     for use in the rate calculation appears accurate.  Unfortunatly, this
     method doesn't seem to return accurate time/rate information for sockets.
     Instead socket information seems most accurate by adding the total
     CPU time usage to the time spent in select() and read()/write(). */

  if(S_ISREG(file_info.st_mode))
    corrected_time = elapsed_time(&start_total, &end_total);
  else
    corrected_time = rusage_elapsed_time(&start_usage, &end_usage) + 
      transfer_time;

  pack_return_values(info, crc_ui, 0, 0, "", corrected_time, NULL, 0);
  return NULL;
}

/***************************************************************************/
/***************************************************************************/

void do_read_write(struct transfer *read_info, struct transfer *write_info)
{
  ssize_t sts;                  /* Return status from read() and write(). */
  size_t bytes_remaining;       /* Number of bytes to move in one loop. */
  size_t bytes_transfered;      /* Number of bytes moved in one loop. */
#ifdef PROFILE
  struct profile profile_data[PROFILE_COUNT]; /* profile data array */
  long profile_count = 0;       /* Index of profile array. */
#endif /*PROFILE*/
  struct timeval start_time;    /* Start of time the thread is active. */
  struct timeval end_time;      /* End of time the thread is active. */
  double time_elapsed;          /* Difference between start and end time. */
  unsigned int crc_ui = 0;      /* Calculated checksum. */

#ifdef PROFILE
  memset(profile_data, 0, sizeof(profile_data));
#endif /*PROFILE*/

  /* Detect (and setup if necessary) the use of memory mapped io. */
  if(setup_mmap_io(read_info))
    return;
  if(setup_mmap_io(write_info))
    return;
  /* Detect (and setup if necessary) the use of direct io. */
  if(setup_direct_io(read_info))
    return;
  if(setup_direct_io(write_info))
    return;
  /* Detect (and setup if necessary) the use of posix io. */
  if(setup_posix_io(read_info))
    return;
  if(setup_posix_io(write_info))
    return;

  errno = 0;
  if((buffer = memalign(sysconf(_SC_PAGESIZE), read_info->block_size)) == NULL)
  {
    pack_return_values(read_info, 0, errno, THREAD_ERROR, "memalign failed",
		       0.0, __FILE__, __LINE__);
    pack_return_values(write_info, 0, errno, THREAD_ERROR, "memalign failed",
		       0.0, __FILE__, __LINE__);
    return;
  }
#ifdef DEBUG
  errno = 0;
  if((stored = malloc(sizeof(int))) == NULL)
  {
    pack_return_values(read_info, 0, errno, THREAD_ERROR, "malloc failed",
		       0.0, __FILE__, __LINE__);
    pack_return_values(write_info, 0, errno, THREAD_ERROR, "malloc failed",
		       0.0, __FILE__, __LINE__);
    return;
  }
  *stored = 0;
#endif /*DEBUG*/

  /* Get the time that the thread started to work on transfering data. */
  if(gettimeofday(&start_time, NULL) < 0)
  {
    pack_return_values(read_info, 0, errno, THREAD_ERROR,
		       "gettimeofday failed", 0.0, __FILE__, __LINE__);
    pack_return_values(write_info, 0, errno, THREAD_ERROR,
		       "gettimeofday failed", 0.0, __FILE__, __LINE__);
    return;
  }
  memcpy(&end_time, &start_time, sizeof(struct timeval));

  while(read_info->bytes > 0 && write_info->bytes > 0)
  {
    /* Since, either one could use mmap io, this needs to be done on both
       every time. */
    if(reinit_mmap_io(read_info))
      return;
    if(reinit_mmap_io(write_info))
      return;

    /* Number of bytes remaining for this loop. */
    bytes_remaining = min(3, (unsigned long long)read_info->bytes,
			  (unsigned long long)read_info->block_size,
			  (unsigned long long)read_info->mmap_left);
    /* Set this to zero. */
    bytes_transfered = 0;

    while(bytes_remaining > 0)
    {
#ifdef PROFILE
      update_profile(1, bytes_remaining, read_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

      /* Handle calling select to wait on the descriptor. */
      if(do_select(read_info))
	return;
      
#ifdef PROFILE
      update_profile(2, bytes_remaining, read_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

#ifdef PROFILE
      update_profile(3, bytes_remaining, read_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

      /* Read in the data. */
      if(read_info->mmap_ptr != MAP_FAILED)
      {
	sts = mmap_read(buffer, bytes_remaining, read_info);
      }
      else
      {
	/* Does double duty in that it also does the direct io read. */
	sts = posix_read((buffer + bytes_transfered),
			 bytes_remaining, read_info);
	if(sts < 0)
	  return;
      }

#ifdef PROFILE
      update_profile(4, sts, read_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

      /* Update this nested loop's counting variables. */
      bytes_remaining -= sts;
      bytes_transfered += sts;
      read_info->bytes -= sts;

#ifdef DEBUG
      *stored = bytes_transfered;
      print_status(stderr, bytes_transfered, bytes_remaining, read_info);
#endif /*DEBUG*/
    }

    /* Initialize the write loop variables. */
    bytes_remaining = bytes_transfered;
    bytes_transfered = 0;

    while (bytes_remaining > 0)
    {

#ifdef PROFILE
      update_profile(5, bytes_remaining, write_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

      /* Handle calling select to wait on the descriptor. */
      if(do_select(write_info))
	return;

#ifdef PROFILE
      update_profile(6, bytes_remaining, write_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

#ifdef PROFILE
      update_profile(7, bytes_remaining, write_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

      if(write_info->mmap_ptr != MAP_FAILED)
      {
	sts = mmap_write(buffer, bytes_remaining, write_info);
      }
      else
      {
	/* Does double duty in that it also does the direct io read. */
	sts = posix_write((buffer + bytes_transfered),
			  bytes_remaining, write_info);
	if(sts < 0)
	  return;
      }

#ifdef PROFILE
      update_profile(8, sts, write_info->fd,
		     profile_data, &profile_count);
#endif /*PROFILE*/

      switch (write_info->crc_flag)
      {
      case 0:
	break;
      case 1:  
	crc_ui=adler32(crc_ui,(void*)((int)buffer+(int)bytes_transfered),sts);
	/*write_info->crc_ui = crc_ui;*/
	break;
      default:
	crc_ui = 0;
	/*write_info->crc_ui = crc_ui;*/
	break;
      }

      /* Handle calling select to wait on the descriptor. */
      bytes_remaining -= sts;
      bytes_transfered += sts;
      write_info->bytes -= sts;

#ifdef DEBUG
      *stored = 0;
      write_info->crc_ui = crc_ui;
      print_status(stderr, bytes_transfered, bytes_remaining, write_info);
#endif /*DEBUG*/
    }
  }
  /* Sync the data to disk and other 'completion' steps. */
  if(finish_write(write_info))
    return;
  if(finish_mmap(read_info))
    return;
  if(finish_mmap(write_info))
    return;

  /* Get the time that the thread finished to work on transfering data. */
  if(gettimeofday(&end_time, NULL) < 0)
  {
    pack_return_values(read_info, 0, errno, THREAD_ERROR,
		       "gettimeofday failed", 0.0, __FILE__, __LINE__);
    pack_return_values(write_info, 0, errno, THREAD_ERROR,
		       "gettimeofday failed", 0.0, __FILE__, __LINE__);
    return;
  }
  time_elapsed = elapsed_time(&start_time, &end_time);

  /* Release the buffer memory. */
  free(buffer);
#ifdef DEBUG
  free(stored);
#endif

#ifdef PROFILE
  print_profile(profile_data, profile_count);
#endif /*PROFILE*/

  pack_return_values(write_info, crc_ui, 0, 0, "", time_elapsed, NULL, 0);
  pack_return_values(read_info, crc_ui, 0, 0, "", time_elapsed, NULL, 0);
  return;
}
/*#endif */

/***************************************************************************
 python defined functions
**************************************************************************/

#ifndef STAND_ALONE

static PyObject *
raise_exception(char *msg)
{
        PyObject	*v;
        int		i = errno;

#   ifdef EINTR
    if ((i==EINTR) && PyErr_CheckSignals()) return NULL;
#   endif

    /* note: format should be the same as in FTT.c */
    v = Py_BuildValue("(s,i,s,i)", msg, i, strerror(i), getpid());
    if (v != NULL)
    {   PyErr_SetObject(EXErrObject, v);
	Py_DECREF(v);
    }
    return NULL;
}

static PyObject *
raise_exception2(struct transfer *rtn_val)
{
    PyObject	*v;
    int		i = rtn_val->errno_val;
  
#   ifdef EINTR
    if ((i==EINTR) && PyErr_CheckSignals()) return NULL;
#   endif

    /* note: format should be the same as in FTT.c */
    /* What does the above comment mean??? */
    v = Py_BuildValue("(s,i,s,i,O,O,O,s,i)",
		      rtn_val->msg, i, strerror(i), getpid(),
		      PyLong_FromLongLong(rtn_val->size),
		      PyFloat_FromDouble(rtn_val->transfer_time),
		      PyFloat_FromDouble(rtn_val->transfer_time),
		      rtn_val->filename, rtn_val->line);
    if (v != NULL)
    {   PyErr_SetObject(EXErrObject, v);
	Py_DECREF(v);
    }
    return NULL;
}

static PyObject *
EXfd_xfer(PyObject *self, PyObject *args)
{
    int		 fr_fd;
    int		 to_fd;
    long long    no_bytes;
    int		 block_size;
    int          array_size;
    long         mmap_size;
    int          direct_io;
    int          mmap_io;
    int          threaded_transfer;
    PyObject     *no_bytes_obj;
    PyObject	 *crc_obj_tp;
    PyObject	 *crc_tp=Py_None;/* optional, ref. FTT.fd_xfer */
    PyObject     *mmap_size_obj;
    int          crc_flag=0; /*0: no CRC 1: Adler32 CRC >1: RFU */
    unsigned int crc_ui;
    struct timeval timeout = {0, 0};
    int sts;
    PyObject	*rr;
    struct transfer reads;
    struct transfer writes;
    
    sts = PyArg_ParseTuple(args, "iiOOiiiOiii|O", &fr_fd, &to_fd,
			   &no_bytes_obj, &crc_obj_tp, &timeout.tv_sec,
			   &block_size, &array_size, &mmap_size_obj,
			   &direct_io, &mmap_io, &threaded_transfer, &crc_tp);
    if (!sts) return (NULL);
    if (crc_tp == Py_None)
	crc_ui = 0;
    else if (PyLong_Check(crc_tp))
	crc_ui = PyLong_AsUnsignedLong(crc_tp);
    else if (PyInt_Check(crc_tp))
	crc_ui = (unsigned)PyInt_AsLong(crc_tp);
    else 
	return(raise_exception("fd_xfer - invalid crc param"));

    if (PyLong_Check(no_bytes_obj))
	no_bytes = PyLong_AsLongLong(no_bytes_obj);
    else if (PyInt_Check(no_bytes_obj))
	no_bytes = (long long)PyInt_AsLong(no_bytes_obj);
    else
	return(raise_exception("fd_xfer - invalid no_bytes param"));
    
    /* see if we are crc-ing */
    if (crc_obj_tp==Py_None)
	crc_flag=0;
    else if (PyInt_Check(crc_obj_tp)) 
	crc_flag = PyInt_AsLong(crc_obj_tp);
    else 
	return(raise_exception("fd_xfer - invalid crc param"));
    if (crc_flag>1 || crc_flag<0)
	fprintf(stderr, "fd_xfer - invalid crc param");

    /* determine mmap array size */
    if (PyLong_Check(mmap_size_obj))
	mmap_size = PyLong_AsLong(mmap_size_obj);
    else if (PyInt_Check(mmap_size_obj))
	mmap_size = (long long)PyInt_AsLong(mmap_size_obj);
    else
	return(raise_exception("fd_xfer - invalid mmap_size param"));

    /*Place the values into the struct.  Some compilers complained when this
      information was placed into the struct inline at initalization.  So it
      was moved here.*/
    memset(&reads, 0, sizeof(reads));
    memset(&writes, 0, sizeof(writes));
    reads.fd = fr_fd;
    reads.mmap_ptr = MAP_FAILED;
    reads.mmap_len = 0;
    reads.size = no_bytes;
    reads.bytes = no_bytes;
    reads.block_size = align_to_page(block_size);
    if(threaded_transfer)
      reads.array_size = array_size;
    else
      reads.array_size = 1;
    reads.mmap_size = mmap_size;
    reads.timeout = timeout;
#ifdef DEBUG
    reads.crc_flag = 1; /*crc_flag;*/
#else
    reads.crc_flag = 0;
#endif
    reads.transfer_direction = -1; /*read*/
    reads.direct_io = direct_io;
    reads.mmap_io = mmap_io;
    writes.fd = to_fd;
    writes.mmap_ptr = MAP_FAILED;
    writes.mmap_len = 0;
    writes.size = no_bytes;
    writes.bytes = no_bytes;
    writes.block_size = align_to_page(block_size);
    if(threaded_transfer)
      writes.array_size = array_size;
    else
      writes.array_size = 1;
    writes.mmap_size = mmap_size;
    writes.timeout = timeout;
    writes.crc_flag = crc_flag;
    writes.transfer_direction = 1; /*write*/
    writes.direct_io = direct_io;
    writes.mmap_io = mmap_io;

    errno = 0;
    if(threaded_transfer)
      do_read_write_threaded(&reads, &writes);
    else
      do_read_write(&reads, &writes);

    /* If the write error is ECANCELED then use the read error, because
       this indicates that the read thread exited first and the ECANCELED
       from the write thread means it knew to exit early. */

    if (writes.exit_status != 0 && writes.errno_val != ECANCELED)
        return (raise_exception2(&writes));
    else if (reads.exit_status != 0)
        return (raise_exception2(&reads));

    rr = Py_BuildValue("(i,O,O,i,s,O,O,s,i)",
		       writes.exit_status, 
		       PyLong_FromUnsignedLong(writes.crc_ui),
		       PyLong_FromLongLong(writes.size),
		       writes.errno_val, writes.msg,
		       PyFloat_FromDouble(reads.transfer_time),
		       PyFloat_FromDouble(writes.transfer_time),
		       writes.filename, writes.line);

    return rr;
}


/***************************************************************************
 inititalization
 **************************************************************************
    Module initialization.   Python call the entry point init<module name>
    when the module is imported.  This should the only non-static entry point
    so it is exported to the linker.

    First argument must be a the module name string.
    
    Second       - a list of the module methods

    Third	- a doumentation string for the module
  
    Fourth & Fifth - see Python/modsupport.c
    */

void
initEXfer()
{
    PyObject	*m, *d;
    
    m = Py_InitModule4("EXfer", EXfer_Methods, EXfer_Doc, 
		       (PyObject*)NULL, PYTHON_API_VERSION);
    d = PyModule_GetDict(m);
    EXErrObject = PyErr_NewException("EXfer.error", NULL, NULL);
    if (EXErrObject != NULL)
	PyDict_SetItemString(d,"error",EXErrObject);
}

#else
/* Stand alone version of exfer is prefered. */

int main(int argc, char **argv)
{
  int fd_in, fd_out;
  struct stat file_info;
  long long size;
  struct timeval timeout = {60, 0};
  unsigned int crc_ui;
  int flags = 0;
  int opt;
  int          block_size = 256*1024;
  int          array_size = 3;
  long         mmap_size = 96*1024*1024;
  int          direct_io = 0;
  int          mmap_io= 0;
  int          threaded_transfer = 0;
  struct transfer reads;
  struct transfer writes;
  
  while((opt = getopt(argc, argv, "tmda:b:l:")) != -1)
  {
    switch(opt)
    {
    case 't':  /* threaded transfer */
      threaded_transfer = 1;
      break;
    case 'm':  /* memory mapped i/o */
      mmap_io = 1;
      break;
    case 'd':  /* direct i/o */
      direct_io = 1;
      flags |= O_DIRECT;
      break;
    case 'a':  /* array size */
      errno = 0;
      if((array_size = (int)strtol(optarg, NULL, 0)) == 0)
      {
	printf("invalid array size(%s): %s\n", optarg, strerror(errno));
	return 1;
      }
      break;
    case 'b':  /* block size */
      errno = 0;
      if((block_size = (int)strtol(optarg, NULL, 0)) == 0)
      {
	printf("invalid array size(%s): %s\n", optarg, strerror(errno));
	return 1;
      }
      break;
    case 'l':  /*mmap length */
      errno = 0;
      if((mmap_size = strtol(optarg, NULL, 0)) == 0)
      {
	printf("invalid mmap size(%s): %s\n", optarg, strerror(errno));
	return 1;
      }
      break;
    default:
      printf("Unknown: %d\n", opt);
    }
  }

  /* Check the number of arguments from the command line. */
  if(argc < 3)
  {
    printf("Usage: test_disk [-tmd] <file1> <files2>\n");
    return 1;
  }
  
  /* Open the input file. */
  errno = 0;
  if((fd_in = open(argv[optind], O_RDONLY | flags)) < 0)
  {
    printf("input open(%s): %s\n", argv[optind], strerror(errno));
    return 1;
  }
  
  /* Open the output file. */
  errno = 0;
  if((fd_out = open(argv[optind+1], O_WRONLY | O_CREAT | O_TRUNC | flags,
		    S_IRUSR | S_IWUSR | S_IRGRP)) < 0)
  {
    printf("output open(%s): %s\n", argv[optind+1], strerror(errno));
    return 1;
  }

  /* Get the file size. */
  errno = 0;
  if(fstat(fd_in, &file_info))
  {
    printf("fstat(): %s\n", strerror(errno));
  }

  /* If reading from /dev/zero, set the size. */
  if(file_info.st_size == 0)
    size = 1024*1024*1024;  /* 1GB */
  else
    size = file_info.st_size;


  /*Place the values into the struct.  Some compilers complained when this
    information was placed into the struct inline at initalization.  So it
    was moved here.*/
  reads.fd = fd_in;
  reads.mmap_ptr = MAP_FAILED;
  reads.mmap_len = 0;
  reads.size = size;
  reads.bytes = size;
  reads.block_size = align_to_page(block_size);
  reads.array_size = array_size;
  reads.mmap_size = mmap_size;
  reads.timeout = timeout;
#ifdef DEBUG
  reads.crc_flag = 1;
#else
  reads.crc_flag = 0;
#endif
  reads.transfer_direction = -1;
  reads.direct_io = direct_io;
  reads.mmap_io = mmap_io;
  writes.fd = fd_out;
  writes.mmap_ptr = MAP_FAILED;
  writes.mmap_len = 0;
  writes.size = size;
  writes.bytes = size;
  writes.block_size = align_to_page(block_size);
  writes.array_size = array_size;
  writes.mmap_size = mmap_size;
  writes.timeout = timeout;
  writes.crc_flag = 1;
  writes.transfer_direction = 1;
  writes.direct_io = direct_io;
  writes.mmap_io = mmap_io;

  /* Do the transfer test. */
  errno = 0;
  if(threaded_transfer)
    do_read_write_threaded(&reads, &writes);
  else
    do_read_write(&reads, &writes);

  printf("Read rate: %f  Write rate: %f\n",
	 size/(1024*1024)/reads.transfer_time,
	 size/(1024*1024)/writes.transfer_time);
}

#endif
