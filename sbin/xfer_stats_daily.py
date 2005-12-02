#!/usr/bin/env python
###############################################################################
# $Author$
# $Date$
# $Id$
#
#  public | xfer_by_day                        | table | enstore
#  public | xfer_by_month                      | table | enstore
# 
###############################################################################
import sys
import os
import string
import time
import math
import configuration_client
import pg
import enstore_constants
import histogram
import thread
PB=1024.*1024.*1024.*1024.*1024.
TB=1024.*1024.*1024.*1024.
GB=1024.*1024.*1024.
MB=1024.*1024.
KB=1024.

def showError(msg):
    sys.stderr.write("Error: " + msg)

def usage():
    print "Usage: %s  <file_family> "%(sys.argv[0],)

def decorate(h,color,ylabel,marker):
    h.set_time_axis(True)
    h.set_ylabel(ylabel)
    h.set_xlabel("Date (year-month-day)")
    h.set_line_color(color)
    h.set_line_width(20)
    h.set_marker_text(marker)
    h.set_marker_type("impulses")
            
exitmutexes=[]

def fill_histograms(i,server_name,server_port,hlist,s1,s2):
    config_server_client   = configuration_client.ConfigurationClient((server_name, server_port))
    acc            = config_server_client.get("database", {})
    db_server_name = acc.get('db_host')
    db_name        = acc.get('dbname')
    db_port        = acc.get('db_port')
    name           = db_server_name.split('.')[0]
    name=db_server_name.split('.')[0]
    print "we are in thread ",i,db_server_name,db_name,db_port
    
    h   = hlist[2*i]
    h1  = hlist[2*i+1]
    
    if db_port:
        db = pg.DB(host=db_server_name, dbname=db_name, port=db_port)
    else:
        db = pg.DB(host=db_server_name, dbname=db_name)
    res=db.query(s1)
    for row in res.getresult():
        if not row:
            continue
        h1.fill(time.mktime(time.strptime(row[0],'%Y-%m-%d %H:%M:%S')),row[1]/TB)
    res=db.query(s2)
    for row in res.getresult():
        if not row:
            continue
        h.fill(float(row[0]),row[1]/TB)
    db.close()
    print "we are done in thread ",i,db_server_name,db_name,db_port
    exitmutexes[i]=1

def plot_bpd():
    #
    # this function creates plots of bytes transferred per day and per month
    # based on data on accounting database (*ensrv6)
    #
    intf  = configuration_client.ConfigurationClientInterface(user_mode=0)
    csc   = configuration_client.ConfigurationClient((intf.config_host, intf.config_port))
    if ( 0 ) :
        acc = csc.get(enstore_constants.ACCOUNTING_SERVER)
        inq = csc.get('inquisitor')
        inq_host=inq.get('www_host').split('/')[2]
    servers=[]
    servers=[]
    servers=csc.get('known_config_servers')
    histograms=[]
    now_time    = time.time()
    t           = time.ctime(time.time())
    Y, M, D, h, m, s, wd, jd, dst = time.localtime(now_time)
    now_time    = time.mktime((Y, M, D, 23, 59, 59, wd, jd, dst))
    start_time  = now_time-31*3600*24
    Y, M, D, h, m, s, wd, jd, dst = time.localtime(start_time)
    start_time = time.mktime((Y, M, D, 23, 59, 59, wd, jd, dst))
    color=1
    s   = histogram.Histogram1D("xfers_total_by_day","Total Bytes Transferred per Day By Enstore",31,float(start_time),float(now_time))
    s.set_time_axis(True)
    plotter=histogram.Plotter("xfers_total_by_day","Total TBytes Transferred per Day By Enstore")
    s_i   = histogram.Histogram1D("integrated_xfers_total_by_day","Integrated total Bytes transferred per Day By Enstore",31,float(start_time),float(now_time))
    s_i.set_time_axis(True)
    iplotter=histogram.Plotter("integrated_xfers_total_by_day","Integrated total Bytes transferred per Day By Enstore")
    w_day=0.
    r_day=0.
    t_day=0.
    n_day=0

    SELECT_STMT="select date,sum(read),sum(write) from xfer_by_day where date between '%s' and '%s' group by date order by date desc"%(time.strftime("%Y-%m-%d",time.localtime(start_time)),
                                                                                                                           time.strftime("%Y-%m-%d",time.localtime(now_time)))

    for server in servers:
        server_name,server_port = servers.get(server)
        if ( server_port != None ):
            config_server_client   = configuration_client.ConfigurationClient((server_name, server_port))
            acc = config_server_client.get(enstore_constants.ACCOUNTING_SERVER)
            db_server_name = acc.get('dbhost')
            db_name        = acc.get('dbname')
            db_port        = acc.get('dbport')
            name           = db_server_name.split('.')[0]
            name=db_server_name.split('.')[0]
            h   = histogram.Histogram1D("xfers_total_by_day_%s"%(name,),"Total Bytes Transferred per Day By  %s"%(server,),31,float(start_time),float(now_time))
            h.set_time_axis(True)
            h.set_ylabel("Bytes")
            h.set_xlabel("Date (year-month-day)")
            h.set_line_color(color)
            h.set_line_width(20)
            color=color+1
            if db_port:
                db = pg.DB(host=db_server_name, dbname=db_name, port=db_port);
            else:
                db = pg.DB(host=db_server_name, dbname=db_name);
            res=db.query(SELECT_STMT)
            for row in res.getresult():
                if not row:
                    continue
                n_day = n_day + 1
                h.fill(time.mktime(time.strptime(row[0],'%Y-%m-%d')),(row[1]+row[2])/TB)
            db.close()

            tmp=s+h
            tmp.set_name("xfer_%s"%(server,))
            tmp.set_data_file_name(server)
            tmp.set_marker_text(server)
            tmp.set_time_axis(True)
            tmp.set_ylabel("TByte/day")
            tmp.set_marker_type("impulses")
            tmp.set_line_color(color)
            tmp.set_line_width(20)
            plotter.add(tmp)
            s=tmp

            integral  = h.integral()

            integral.set_marker_text(server)
            integral.set_marker_type("impulses")
            integral.set_ylabel("TB");

            tmp=s_i+integral
            tmp.set_name("integrated_xfers_daily_%s"%(integral.get_marker_text(),))
            tmp.set_data_file_name("integrated_xfers_daily_%s"%(integral.get_marker_text(),))
            tmp.set_marker_text(integral.get_marker_text())
            tmp.set_time_axis(True)
            tmp.set_ylabel(integral.get_ylabel())
            tmp.set_marker_type(integral.get_marker_type())
            tmp.set_line_color(color)
            tmp.set_line_width(20)
            iplotter.add(tmp)
            s_i=tmp


    plotter.reshuffle()
    tmp=plotter.get_histogram_list()[0]

    t_day_max = 0.
    i_day_max = 0

    t_day_min = 1.e+32
    i_day_min = 0

    for i in range(tmp.n_bins()) :
        t_day = t_day + tmp.get_bin_content(i)
        if (  tmp.get_bin_content(i) > t_day_max ) :
            t_day_max = tmp.get_bin_content(i)
            i_day_max = i
        if ( tmp.get_bin_content(i) < t_day_min and  tmp.get_bin_content(i) > 0 ) :
            t_day_min = tmp.get_bin_content(i)
            i_day_min = i
            
    tmp.set_line_color(1)

    delta =  tmp.binarray[i_day_max]*0.05
    
    tmp.add_text("set label \"%5d\" at \"%s\",%f right rotate font \"Helvetica,12\"\n"%(tmp.binarray[i_day_max]+0.5,
        time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(tmp.get_bin_center(i_day_max))),
        tmp.binarray[i_day_max]+delta,))

    tmp.add_text("set label \"%5d\" at \"%s\",%f right rotate font \"Helvetica,12\"\n"%(tmp.binarray[i_day_min]+0.5,
        time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(tmp.get_bin_center(i_day_min))),
        tmp.binarray[i_day_min]+delta,))

    tmp.add_text("set label \"Total :  %5d TB  \" at graph .8,.8  font \"Helvetica,13\"\n"%(t_day+0.5,))
    tmp.add_text("set label \"Max   :  %5d TB (on %5s) \" at graph .8,.75  font \"Helvetica,13\"\n"%(t_day_max+0.5,
                                                                                                 time.strftime("%m-%d",time.localtime(tmp.get_bin_center(i_day_max))),))
    tmp.add_text("set label \"Min   :  %5d TB (on %5s) \" at graph .8,.70  font \"Helvetica,13\"\n"%(t_day_min+0.5,
                                                                                                 time.strftime("%m-%d",time.localtime(tmp.get_bin_center(i_day_min))),))
    tmp.add_text("set label \"Mean  :  %5d TB \" at graph .8,.65  font \"Helvetica,13\"\n"%(t_day /  (tmp.n_bins()-1)+0.5,))



    plotter.plot()

    iplotter.reshuffle()
    tmp=iplotter.get_histogram_list()[0]
    tmp.set_line_color(1)
    tmp.set_marker_type("impulses")
    iplotter.plot()

def plot_bytes():
    #
    # This function plots bytes written/deleted to/from Enstore base on data in file and volume tables
    # from *ensrv0 postgress databases damn slow
    #
    intf  = configuration_client.ConfigurationClientInterface(user_mode=0)
    csc   = configuration_client.ConfigurationClient((intf.config_host, intf.config_port))
    servers=[]
    servers=[]
    servers=csc.get('known_config_servers')
    histograms=[]
    
    now_time    = time.time()
    t           = time.ctime(time.time())
    Y, M, D, h, m, s, wd, jd, dst = time.localtime(now_time)
    
    now_time    = time.mktime((Y, M, D, 23, 59, 59, wd, jd, dst))
    start_time  = now_time-31*3600*24
    Y, M, D, h, m, s, wd, jd, dst = time.localtime(start_time)
    start_time = time.mktime((Y, M, D, 23, 59, 59, wd, jd, dst))

    s = histogram.Histogram1D("writes_total_by_day","Total bytes written per day by Enstore",31,float(start_time),float(now_time))
    s1 = histogram.Histogram1D("deletes_total_by_day","Total bytes deleted  per day from Enstore",31,float(start_time),float(now_time))

    s.set_time_axis(True)
    s1.set_time_axis(True)

    plotter=histogram.Plotter("writes_total_by_day","Total TBytes written per day by Enstore")
    plotter1=histogram.Plotter("deletes_total_by_day","Total TBytes deleted per day from Enstore")

    s_i = histogram.Histogram1D("writes_total_by_day","Integrated Total bytes written per day by Enstore",31,float(start_time),float(now_time))
    s1_i = histogram.Histogram1D("deletes_total_by_day","Integrated Total bytes deleted  per day from Enstore",31,float(start_time),float(now_time))

    s_i.set_time_axis(True)
    s1_i.set_time_axis(True)

    iplotter=histogram.Plotter("integrated_writes_total_by_day","Integrated Total TBytes written per day by Enstore")
    iplotter1=histogram.Plotter("integrated_deletes_total_by_day","Integrated Total TBytes deleted per day from Enstore")

    SELECT_DELETED_BYTES ="select to_char(state.time, 'YY-MM-DD HH:MM:SS'), sum(file.size)::bigint from file, state where state.volume=file.volume and state.value='DELETED' and state.time between '%s' and '%s' group by state.time order by state.time desc"%(time.strftime("%Y-%m-%d",time.localtime(start_time)), time.strftime("%Y-%m-%d",time.localtime(now_time)))
    
    SELECT_WRITTEN_BYTES ="select substr(bfid,5,10), size from file, volume  where file.volume = volume.id and not label like '%.deleted' and media_type != 'null' and substr(bfid,5,10)::bigint between "+str(start_time)+" and "+str(now_time)


    i = 0
    color=1
    for server in servers:
        server_name,server_port = servers.get(server)
#        if (server == "stken") : continue
        if ( server_port != None ):
            h   = histogram.Histogram1D("writes_by_day_%s"%(server,),"Total Bytes Written by Day By %s"%(server,),31,float(start_time),float(now_time))
            decorate(h,color,"TB/day",server)
            histograms.append(h)

            h   = histogram.Histogram1D("deletes_by_day_%s"%(server,),"Total Bytes Deleted by Day By %s"%(server,),31,float(start_time),float(now_time))
            decorate(h,color,"TB/day",server)
            histograms.append(h)

            exitmutexes.append(0)
            thread.start_new(fill_histograms, (i,server_name,server_port,histograms,SELECT_DELETED_BYTES,SELECT_WRITTEN_BYTES))
            i=i+1
            color=color+1

    while 0 in exitmutexes: pass

    i = 0
    for i in range(len(histograms)/2):
        h  = histograms[i*2]
        h1 = histograms[2*i+1]
        color = i + 2
        tmp=s+h
        tmp.set_name("writes_daily_%s"%(h.get_marker_text(),))
        tmp.set_data_file_name("writes_daily_%s"%(h.get_marker_text(),))
        tmp.set_marker_text(h.get_marker_text())
        tmp.set_time_axis(True)
        tmp.set_ylabel(h.get_ylabel())
        tmp.set_marker_type(h.get_marker_type())
        tmp.set_line_color(color)
        tmp.set_line_width(20)
        plotter.add(tmp)
        s=tmp

        tmp=s1+h1
        tmp.set_name("deletes_daily_%s"%(h1.get_marker_text(),))
        tmp.set_data_file_name("deletes_daily_%s"%(h1.get_marker_text(),))
        tmp.set_marker_text(h1.get_marker_text())
        tmp.set_time_axis(True)
        tmp.set_ylabel(h1.get_ylabel())
        tmp.set_marker_type(h1.get_marker_type())
        tmp.set_line_color(color)
        tmp.set_line_width(20)
        plotter1.add(tmp)
        s1=tmp

        integral = h.integral()
        integral1 = h1.integral()

        integral.set_marker_text(h.get_marker_text())
        integral.set_marker_type("impulses")
        integral.set_ylabel("TB");

        integral1.set_marker_text(h1.get_marker_text())
        integral1.set_marker_type("impulses")
        integral1.set_ylabel("TB");

        tmp=s_i+integral
        tmp.set_name("integrated_writes_daily_%s"%(integral.get_marker_text(),))
        tmp.set_data_file_name("integrated_writes_daily_%s"%(integral.get_marker_text(),))
        tmp.set_marker_text(integral.get_marker_text())
        tmp.set_time_axis(True)
        tmp.set_ylabel(integral.get_ylabel())
        tmp.set_marker_type(integral.get_marker_type())
        tmp.set_line_color(color)
        tmp.set_line_width(20)
        iplotter.add(tmp)
        s_i=tmp

        tmp=s1_i+integral1
        tmp.set_name("integrated_deletes_daily_%s"%(integral1.get_marker_text(),))
        tmp.set_data_file_name("integrated_deletes_daily_%s"%(integral1.get_marker_text(),))
        tmp.set_marker_text(integral1.get_marker_text())
        tmp.set_time_axis(True)
        tmp.set_ylabel(integral1.get_ylabel())
        tmp.set_marker_type(integral1.get_marker_type())
        tmp.set_line_color(color)
        tmp.set_line_width(20)
        iplotter1.add(tmp)
        s1_i=tmp
        i=i+1

    plotters=[]
    plotters.append(plotter)
    plotters.append(plotter1)
    plotters.append(iplotter)
    plotters.append(iplotter1)

    for p in plotters:
        p.reshuffle()
        tmp=p.get_histogram_list()[0]
        tmp.set_line_color(1)
        tmp.set_marker_type("impulses")
        p.plot()

if __name__ == "__main__":
    plot_bpd()
    plot_bytes()
    sys.exit(0)
