#include "acssys.h"

#include "acsapi.h"
/* #include "cl_pub.h" */
#include <Python.h>

/*
 *  Get the acknowledgement response
 */
#define SSI_TIMEOUT 300

static PyObject *STKErrObject;

int STKerr(char *caller, char *location, STATUS status)
{
 printf("STK err: %s - %s - code %d - %s\n", caller, location, status, cl_status(status));
 return(status);
}

STATUS get_ack()           /* common get acknowledgment function*/
{

    SEQ_NO                seq_nmbr;     /* command identification number     */
    REQ_ID                req_id;       /* response request identification   */
    STATUS                status;       /* command return status structure   */
    ACS_RESPONSE_TYPE     type;         /* final response structure          */
    ALIGNED_BYTES         rbuf[(MAX_MESSAGE_SIZE/sizeof(ALIGNED_BYTES)+1)];

    /*
    **  Call acs_response to get the acknowledgement response.  If the
    **  acknowledgement message fails, write an error message to stderr,
    **  otherwise write the request id to stdout.
    */

    status = acs_response(SSI_TIMEOUT,&seq_nmbr,&req_id,&type, rbuf);
    return(status);
}
/*
 *  Get the final (or next) response
 */

STATUS get_next( ALIGNED_BYTES *p_rbuf,
                int p_size,
                ACS_RESPONSE_TYPE *p_type) /* common get final (or next)func */

{

    SEQ_NO                seq_nmbr;     /* command identification number     */
    REQ_ID                req_id;       /* response request identification   */
    STATUS                status;       /* command return status structure   */
    ACS_RESPONSE_TYPE     type;         /* final response structure          */
    ALIGNED_BYTES         rbuf[(MAX_MESSAGE_SIZE/sizeof(ALIGNED_BYTES)+1)];

    /*
    **  Call acs_response to get an intermediate and/or final response.  If
    **  the message response fails, write an error message to stderr, otherwise
    **  return the message and type to the caller.
    */

    status = acs_response(SSI_TIMEOUT,&seq_nmbr,&req_id,&type, rbuf);

    memcpy((ALIGNED_BYTES)p_rbuf,rbuf,p_size);
    *p_type = type;
    return(status);
}

STATUS STKmount(SEQ_NO p_s,
             char p_volume[],
             DRIVEID p_drv_id,
             BOOLEAN p_readonly,
             LOCKID p_lock_id)          /* mount function                    */

{
    VOLID               vol_id;         /* volume structure                  */
    ACS_MOUNT_RESPONSE  *mp;            /* mount response structure          */
    ALIGNED_BYTES       rbuf[(MAX_MESSAGE_SIZE/sizeof(ALIGNED_BYTES)+1)];
    STATUS              status;         /* command return status structure   */
    ACS_RESPONSE_TYPE   type;           /* final response structure          */
    int                 size;           /* final response size               */
    BOOLEAN             bypass = FALSE; /* bypass checking flag              */
    char                *drive_name;    /* drive name                        */

    /*
    **  Call mount to mount the requested volume on the drive.  If the mount
    **  itself, or the mount status fails, write an error message to stderr,
    **  otherwise write the result of the mount to stdout.
    */

    (void)strcpy(vol_id.external_label,p_volume);

    if ((status = acs_mount(
		p_s,		/* client defined number returned in the response */
		p_lock_id,	/* Lock the drive with this id or NO_LOCK */
		vol_id	,	/* Id of the tape cartridge to be mounted */
		p_drv_id,	/* Id of the drive where the tape cartridge is mounted */
		p_readonly,	/* If TRUE, the volume will be mounted readonly */
		bypass))	/* If TRUE, bypass volser and media verification */ 
                         != STATUS_SUCCESS)
        return(STKerr("Mount", "transmit", status));

    if ((status = get_ack()) != STATUS_SUCCESS) 
        return(STKerr("Mount", "get_ack", status));
    size = sizeof(ACS_MOUNT_RESPONSE);
    if ((status = get_next(rbuf,size,&type)) != STATUS_SUCCESS) 
        return(STKerr("Mount", "get_next", status));

    mp = (ACS_MOUNT_RESPONSE *)rbuf;
    if (mp->mount_status != STATUS_SUCCESS) 
        return(STKerr("Mount", "status_field", mp->mount_status));
    return(mp->mount_status);

}

STATUS STKdismount(SEQ_NO p_s,
                char p_volume[],
                DRIVEID p_drv_id,
                LOCKID p_lock_id)      /* dismount function                 */

{

    ACS_DISMOUNT_RESPONSE *dp;          /* dismount response structure       */
    VOLID                 vol_id;       /* volume id structure               */
    char                  cmd_buf[256]; /* system call command buffer        */
    LOCKID                lock_id;      /* lock value structure              */
    ALIGNED_BYTES         rbuf[(MAX_MESSAGE_SIZE/sizeof(ALIGNED_BYTES)+1)];
    STATUS                status;       /* command return status structure   */
    ACS_RESPONSE_TYPE     type;         /* final response structure          */
    int                   size;         /* final response structure size     */
    char                  *drive_name;  /* drive name                        */
    int                   ret;          /* function return code              */

    /*
    **  If the force flag is false, issue an unload for the requested drive,
    **  and if the unload fails write an error message to stderr, and set the
    **  force flag true.  Then call dismount to dismount the requested volume
    **  from the drive.  If the dismount itself, or the dismount status fails,
    **  write an error message to stderr, otherwise write the result of
    **  dismount to stdout.
    */

    (void)strcpy(vol_id.external_label,p_volume);

    if ((status = acs_dismount(
		p_s,		/* client defined number returned in the response*/
		p_lock_id,	/* Lock the drive with this id */
		vol_id,		/* Id of the tape cartridge to be mounted */
		p_drv_id,	/* Id of the drive where the tape cartridge is mounted */
		TRUE))		/* Force Dismount */
		 != STATUS_SUCCESS) 
        return(STKerr("Mount", "transmit", status));

    if ((status = get_ack()) != STATUS_SUCCESS) 
        return(STKerr("Mount", "get_ack", status));

    size = sizeof(ACS_DISMOUNT_RESPONSE);
    if ((status = get_next(rbuf,size,&type)) != STATUS_SUCCESS) 
        return(STKerr("Mount", "get_next", status));

    dp = (ACS_DISMOUNT_RESPONSE *)rbuf;
    if (dp->dismount_status != STATUS_SUCCESS) 
        return(STKerr("Dismount", "status_field", dp->dismount_status));

    return(dp->dismount_status);
}

/*
   Convert a drive from ascii of the form "lsm,acs,panel,drive" to a DRIVEID struct.
   see  ./h/api/ident_api.h.   There must be a stk api but I can't find it.
   There is NO ERROR CHECKING - intended for use in enstore
*/
void  asc2STKdrv( char *drive,  DRIVEID *stkdrv)
{
  stkdrv->panel_id.lsm_id.lsm=strtol(drive,&drive,0); drive++;
  stkdrv->panel_id.lsm_id.acs=strtol(drive,&drive,0); drive++;
  stkdrv->panel_id.panel =    strtol(drive,&drive,0); drive++;
  stkdrv->drive =             strtol(drive,&drive,0);
}

static char STK_Doc[] =  "STK Robot Mount and Dismount";
static char Mount_Doc[] =  "Mount a tape";
static char Dismount_Doc[] =  "Dismount a tape";

static PyObject* Mount(PyObject *self, PyObject *args)
{
  char *vol;
  char *drive;
  DRIVEID stkdrv;
  int stat;
  /*
        Get the arguements
  */
  PyArg_ParseTuple(args, "ss", &vol, &drive);
  asc2STKdrv(drive, &stkdrv);
  stat = STKmount(0, vol, stkdrv, 0, NO_LOCK_ID);
  return(Py_BuildValue("i",stat ));
}

static PyObject* Dismount(PyObject *self, PyObject *args)
{
  char *vol;
  char *drive;
  DRIVEID stkdrv;
  int stat;
  /*
        Get the arguements
  */
  PyArg_ParseTuple(args, "ss", &vol, &drive);
  asc2STKdrv(drive, &stkdrv);
  stat = STKdismount(0, vol, stkdrv, NO_LOCK_ID);
  return(Py_BuildValue("is",stat,cl_status(stat) ));
}

/*
   Module Methods table.

   There is one entry with four items for for each method in the module

   Entry 1 - the method name as used  in python
         2 - the c implementation function
         3 - flags
         4 - method documentation string
*/

static PyMethodDef STK_Methods[] = {
  { "Mount", Mount, 1, Mount_Doc},
  { "Dismount", Dismount, 1, Dismount_Doc},
  {0,     0}        /* Sentinel */
};

/*
   Module initialization.   Python call the entry point init<module name>
   when the module is imported.  This should the only non-static entry point
   so it is exported to the linker.

   The Py_InitModule4 is not in the python 1.5 documentation but is copied
   from the oracle module.  It extends Py_InitModule with documentation
   and seems useful.

   First argument must be a the module name string.

   Seond        - a list of the module methods

   Third        - a doumentation string for the module

   Fourth & Fifth - see Python/modsupport.c

*/

void initSTK()
{
  PyObject *m, *d;

  m=  Py_InitModule4("STK", STK_Methods, STK_Doc,
                        (PyObject*)NULL,PYTHON_API_VERSION);
  d = PyModule_GetDict(m);
  STKErrObject = PyErr_NewException("STK.error", NULL, NULL);
  if (STKErrObject != NULL)
             PyDict_SetItemString(d,"error",STKErrObject);
}

