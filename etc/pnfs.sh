#!/bin/sh

if [ "${1:-}" = "-x" ] ; then set -xv; shift; fi
set -u  # force better programming and ability to use check for not set

p-cmd()   { echo "\"`dirname $1`/$2(`basename $1`)\"" ; }

p-use()   { uj="Usage:  p-use filename [container number]"
            if [ -z "${1-}" ] ; then echo $uj; return; fi
            if [ -z "${2-}" ] ; then c=".(use)(1)"; else c=".(use)($2)"; fi
            p-cmd $1 $c ; }

pcat()    { eval cat  `p-use $@` ; }
pmore()   { eval more `p-use $@` ; }

pdir()    { eval ls -alsF `p-use $@` ; }

pecho()   { uj="Usage:  pecho text file [container number]"
            if [ -z "${1-}" -o  -z "${2-}" ] ; then echo $uj; return; fi
            pt="$1"
            shift 1
            eval echo -n "$pt" \>\| `p-use $@` ; }

prm()     { eval echo -n "" \>\| `p-use $@` ; }

pcp()     { uj="Usage:  pcp file pnfsfile [container number]"
            if [ -z "${1-}" -o  -z "${2-}" ] ; then echo $uj; return; fi
            pt="$1"
            shift 1
            eval cp "$pt" `p-use $@` ; }

psize()   { u_j="Usage:  psize filename size";
            if [ -z "${1-}" -o  -z "${2-}" ] ; then echo $uj; return; fi
            eval touch  "\"`dirname $1`/.(fset)(`basename $1`)(size)($2)\"" ; }

pid()     { uj="Usage:  pid file"
            if [ -z "${1-}" ] ; then echo $uj; return; fi
            eval cat  `p-cmd $1 ".(id)"` ; }

pshowid() { u_n="Usage: pshowid id"
            if [ -z "${1-}" ] ; then echo $u_n; return; fi
            eval cat  "\".(showid)($1)\"" ; }

pconst()  { uj="Usage:  pid file"
            if [ -z "${1-}" ] ; then echo $uj; return; fi
            eval cat   `p-cmd $1 ".(const)"` ; }

pnameof() { u_n="Usage: pnameof id"
            if [ -z "${1-}" ] ; then echo $u_n; return; fi
            eval cat  "\".(nameof)($1)\"" ; }

pparent() { u_p="Usage: pnameof id"
            if [ -z "${1-}" ] ; then echo $u_p; return; fi
            eval cat  "\".(parent)($1)\"" ; }

pcounters() { u_p="Usage: pcounters"
            eval cat  "\".(get)(counters)\"" ; }

pcursor() { u_p="Usage: pcursor"
            eval cat  "\".(get)(cursor)\"" ; }

pCursor() { waste=/tmp/gc-$$
            rm -f $waste
            cat ".(get)(cursor)" >$waste
            if [ $? -ne 0 ] ; then problem "FAILED : Can get .(get)(cursor) " ; return ; fi
            . $waste
            /bin/echo  " dirID : $dirID ; dirPerm : $dirPerm ; mountID : $mountID "
            mode=`/bin/echo $dirPerm | awk '{ print substr( $1 , 15, 1 ) }'`
            level=`/bin/echo $dirPerm | awk '{ print substr( $1 , 16 , 1 ) }'`
            /bin/echo " We at level $level in mode $mode "
            if [ $mode = "2" ] ; then
              /bin/echo "The I/O mode of level 0 is DISABLED "
            else
              /bin/echo "The I/O mode of level 0 is ENABLED "
            fi ; }

pio()     { u_j="Usage:  pio filename";
            if [ -z "${1-}" ] ; then echo $uj; return; fi
            eval touch  "\"`dirname $1`/.(fset)(`basename $1`)(io)\"" ; }


p-tag()   { uj="Usage: p-tag tagname"
            if [ -z "${1-}" ] ; then echo $uj; return; fi
            echo "\".(tag)($1)\"" ; }

ptcat()   { eval cat  `p-tag $@` ; }
ptmore()  { eval more `p-tag $@` ; }

ptdir()   { cat ".(tags)(all)" ; }

ptags()   { for i in `ptdir`; do v=`eval cat '$i'`; echo $i " = " $v; done ; }

ptecho()  { uj="Usage:  ptecho text tagname"
            if [ -z "${1-}" -o  -z "${2-}" ] ; then echo $uj; return; fi
            pt="$1"
            shift 1
            eval echo -n "$pt" \>\| `p-tag $@` ; }

ptrm()    { echo "The attempt to remove a tag results in an unpredictable behavior and may corrupt the entire directory"
            echo "If you insist on doing this, try   eval echo -n \"\" >| `p-tag tagname`" ; }
