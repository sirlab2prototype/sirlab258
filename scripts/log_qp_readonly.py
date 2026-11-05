#!/usr/bin/env python3
"""ROS subscribers only; no SDK, services, publishers or control commands."""
import argparse,json,time,threading,queue
from pathlib import Path

def analyze(path):
    import numpy as np
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    states=[r for r in rows if r.get('topic')=='/joint_states'];commands=[r for r in rows if r.get('topic')=='/capstone/teleop_action']
    out={'states':len(states),'commands':len(commands),'stop_latency_s':None,'note':'receipt timestamps; q velocity/acceleration finite differences are sampled estimates, not controller peaks. No exact SDK send/stop ACK available.'}
    valid=[]
    for r in states:
        d=r['data'];names=d['name']
        if all('joint'+str(i) in names for i in range(6)):
            q=[d['position'][names.index('joint'+str(i))] for i in range(6)]
            if np.isfinite(q).all():valid.append((r['received_ns']/1e9,q))
    if len(valid)>=4:
        t=np.array([v[0] for v in valid]);q=np.array([v[1] for v in valid]);dt=np.diff(t);good=dt>0
        v=np.diff(q,axis=0)[good]/dt[good,None];tv=((t[1:]+t[:-1])/2)[good]
        out['interval_s']={'min':float(min(dt)),'max':float(max(dt))}
        if len(v):out['velocity_abs_max_rad_s']=np.max(abs(v),axis=0).tolist()
        if len(v)>=2:
            a=np.diff(v,axis=0)/np.diff(tv)[:,None]
            out['acceleration_abs_quantiles_rad_s2']={str(p):np.percentile(abs(a),p,axis=0).tolist() for p in (50,95,99,100)}
        errors=[];ages=[];cmds=sorted(commands,key=lambda r:r['received_ns']);k=0
        for stamp,pos in valid:
            while k+1<len(cmds) and cmds[k+1]['received_ns']<=stamp*1e9:k+=1
            if cmds and cmds[k]['received_ns']<=stamp*1e9:
                d=cmds[k]['data'];names=d['name']
                if all('joint'+str(i) in names for i in range(6)):
                    target=np.array([d['position'][names.index('joint'+str(i))] for i in range(6)])
                    errors.append(abs(target-pos));ages.append(stamp-cmds[k]['received_ns']/1e9)
        if errors:out.update(tracking_abs_quantiles_rad={str(p):np.percentile(errors,p,axis=0).tolist() for p in (50,95,99,100)},matched_command_age_s_max=max(ages),tracking_note='last received command, includes idle/stale holds; inspect ages before treating as dynamic tracking')
    vel=[r['data'].get('velocity',[]) for r in states]
    vel=[v for v in vel if len(v)==6 and np.isfinite(v).all()]
    if vel:out['reported_qdot_abs_max_rad_s']=np.max(abs(np.array(vel)),axis=0).tolist()
    out['logger_end']=[r for r in rows if r.get('kind')=='end']
    out['valid_complete_log']=bool(out['logger_end'] and out['logger_end'][-1].get('drops')==0 and out['logger_end'][-1].get('write_error') is None)
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);p.add_argument('--analyze',type=Path);a=p.parse_args()
    if a.analyze:
        print(json.dumps(analyze(a.analyze),indent=2));return
    if not a.output:p.error('--output required')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    stream=a.output.open('x');q=queue.Queue(maxsize=10000);done=threading.Event();stats={'drops':0,'write_error':None}
    def writer():
        try:
            while not done.is_set() or not q.empty():
                try:row=q.get(timeout=.1)
                except queue.Empty:continue
                stream.write(json.dumps(row,allow_nan=False)+'\n');stream.flush()
        except Exception as exc:stats['write_error']=repr(exc)
    worker=threading.Thread(target=writer,daemon=True);worker.start()
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
    from sensor_msgs.msg import JointState
    from std_msgs.msg import String
    from rosidl_runtime_py.convert import message_to_ordereddict
    rclpy.init();node=Node('qp_measurement_readonly')
    def emit(row):
        try:q.put_nowait(row)
        except queue.Full:stats['drops']+=1
    emit({'kind':'session','joint_order':['joint'+str(i) for i in range(6)],'units':'JointState rad; raw velocity if provided rad/s','timestamps':'header ROS stamp + callback receipt monotonic; not SDK send/sensor time','SDK_connections':0})
    def callback(topic,msg):emit({'topic':topic,'received_ns':time.monotonic_ns(),'system_ns':time.time_ns(),'data':message_to_ordereddict(msg)})
    for topic,typ in [('/joint_states',JointState),('/capstone/teleop_action',JointState),('/capstone/action_context',String),('/capstone/teleop_status',String)]:
        qos=QoSProfile(depth=100,reliability=ReliabilityPolicy.BEST_EFFORT,durability=DurabilityPolicy.VOLATILE)
        node.create_subscription(typ,topic,lambda msg,t=topic:callback(t,msg),qos)
    def status():
        emit({'kind':'graph','received_ns':time.monotonic_ns(),'nodes':node.get_node_names(),'publishers':{t:[i.node_name for i in node.get_publishers_info_by_topic(t)] for t in ('/joint_states','/capstone/teleop_action')}})
        print(json.dumps({'queue':q.qsize(),**stats}),flush=True)
    node.create_timer(1.,status)
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node();rclpy.try_shutdown();emit({'kind':'end',**stats});done.set();worker.join(3)
        if worker.is_alive():print('Logger drain incomplete; log invalid')
        else:stream.close()
if __name__=='__main__':main()
