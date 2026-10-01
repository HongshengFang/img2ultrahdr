"""Sample RSS for one test process and its descendants (macOS ps, no install)."""
import argparse,json,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('pid',type=int);p.add_argument('output',type=Path);args=p.parse_args()
peak=0;count=0;started=time.time();peak_processes=[];trend=[]
while True:
    rows={}
    for line in subprocess.check_output(['ps','-axo','pid=,ppid=,rss=,comm='],text=True).splitlines():
        fields=line.split(None,3)
        if len(fields)==4: rows[int(fields[0])]=(int(fields[1]),int(fields[2]),fields[3])
    if args.pid not in rows:break
    owned={args.pid}
    while True:
        extra={pid for pid,(ppid,_,_) in rows.items() if ppid in owned}
        if extra<=owned:break
        owned |= extra
    total=sum(rows[pid][1] for pid in owned)
    count+=1
    if count==1 or count%30==0:
        trend.append({'seconds':time.time()-started,'rss_mib':total/1024,'process_count':len(owned)})
    if total>peak:
        peak=total;peak_processes=[{'pid':pid,'rss_kib':rows[pid][1],'command':rows[pid][2]} for pid in sorted(owned)]
    report={'measurement':'sum of resident bytes, process and live descendants; excludes other tests',
            'root_pid':args.pid,'samples':count,'elapsed_seconds':time.time()-started,
            'peak_rss_mib':peak/1024,'peak_processes':peak_processes,'trend':trend}
    temp=args.output.with_suffix('.partial');temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(args.output)
    time.sleep(1)
