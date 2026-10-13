"""Independent objects -> real S7/Modbus memories, fault and reconnection."""
import argparse
import json
import subprocess
import time
import urllib.request
import urllib.error


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8003')
    parser.add_argument('--s7-image', default='anvil/plc-s7:field-v1')
    parser.add_argument('--modbus-image', default='anvil/modbus-tcp:field-v1')
    args=parser.parse_args()
    def call(path, method='GET', data=None):
        request=urllib.request.Request(args.base_url+path, method=method, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=60) as response:
                raw=response.read();return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            detail=error.read().decode('utf-8',errors='replace')
            error.add_note(f'{method} {path} response: {detail}')
            raise
    fields=[{'object_id':'v','type':'valve','name':'Valve','response_seconds':1}, {'object_id':'s','type':'analog_sensor','name':'Sensor','min_value':0,'max_value':100,'unit':'%'}, {'object_id':'m','type':'motor','name':'Motor','response_seconds':.2}, {'object_id':'tank','type':'tank','name':'Leaking tank','initial_level_liters':20,'leak_flow_lpm':6}, {'object_id':'pressure','type':'analog_sensor','name':'Tank pressure','min_value':0,'max_value':20,'unit':'kPa','simulation':{'mode':'process'}}]
    for attempt in range(20):
        try:
            call('/api/ready');break
        except (OSError, urllib.error.URLError):
            time.sleep(.5)
    else:raise RuntimeError('Anvil is not ready')
    payload={'name':'AUDIT independent field','network':{'subnet':'172.30.245.0/24'},'devices':[],'field_objects':fields,'signal_connections':[],'process_connections':[{'connection_id':'tank-pressure','source':{'object_id':'tank','port':'pressure'},'target':{'object_id':'pressure','port':'measurement'}}]}
    scenario=call('/api/scenarios','POST',payload);sid=scenario['id'];root=f'/api/scenarios/{sid}'
    try:
        result=call(root+'/deploy','POST')
        assert len(result['containers'])==1
        objects=call(root+'/field/values')['objects']
        assert len(objects)==5
        initial=next(item['volume_liters'] for item in objects if item['object_id']=='tank')
        tank=next(item for item in objects if item['object_id']=='tank')
        pressure=next(item for item in objects if item['object_id']=='pressure')
        assert 0 < tank['pressure_kpa'] < .5 and abs(pressure['value']-tank['pressure_kpa']) < .001
        time.sleep(1.2)
        tank=next(item for item in call(root+'/field/values')['objects'] if item['object_id']=='tank')
        assert 0 < initial-tank['volume_liters'] < .5 and tank['leak_lpm']==6
        call(root+'/stop','POST')
        payload['devices']=[{'node_id':'plc','device_type':'plc-s7','name':'PLC','ip':'172.30.245.10','config':{'components':[
            {'type':'io_channel','name':'Order','direction':'output','data_type':'BOOL'},
            {'type':'io_channel','name':'Feedback','direction':'input','data_type':'BOOL'},
            {'type':'io_channel','name':'Measure','direction':'input','data_type':'REAL','unit':'%'},
            {'type':'io_channel','name':'Tank pressure','direction':'input','data_type':'REAL','unit':'kPa'},
        ]}}, {'node_id':'mb','device_type':'modbus-tcp','name':'Modbus','ip':'172.30.245.11','config':{'points':[
            {'type':'coil','name':'Order','address':0}, {'type':'coil','name':'Feedback','address':1}, {'type':'input_register','name':'Measure','address':0,'unit':'%'}, {'type':'input_register','name':'Tank pressure','address':1,'unit':'kPa'}
        ]}}]
        def link(cid,oid,port,node,address,command=False,conversion=None):
            f={'kind':'field','object_id':oid,'port':port};d={'kind':'device','node_id':node,'address':address}
            return {'connection_id':cid,'source':d if command else f,'target':f if command else d, **({'conversion':conversion} if conversion else {})}
        payload['signal_connections']=[link('order','v','command','plc','DB1.DBX0.0',True),link('feedback','v','state','plc','DB1.DBX1.0'),link('measure','s','value','plc','DB1.DBD2'),link('pressure-s7','pressure','value','plc','DB1.DBD6'),link('mb-order','m','command','mb','coil:0',True),link('mb-feedback','m','state','mb','coil:1'),link('mb-measure','s','value','mb','input_register:0',conversion={'input_min':0,'input_max':100,'output_min':0,'output_max':1000}),link('pressure-mb','pressure','value','mb','input_register:1',conversion={'input_min':0,'input_max':20,'output_min':0,'output_max':1000})]
        call(root,'PUT',payload);call(root+'/deploy','POST')
        call(root+'/field/tank/control','POST',{'demand_flow_lpm':12})
        snapshot=call(root+'/field/values')
        tank=next(item for item in snapshot['objects'] if item['object_id']=='tank')
        assert tank['demand_lpm']==12 and tank['effective_demand_lpm']==12 and tank['outflow_lpm']==18
        pressure=next(item for item in snapshot['objects'] if item['object_id']=='pressure')
        assert abs(pressure['value']-tank['pressure_kpa']) < .001
        network='anvil-'+sid[:12]
        probe=r'''import struct,time
from snap7.client import Client
s=Client();s.connect('172.30.245.10',0,1,tcp_port=102)
try:
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  raw=s.db_read(1,0,10)
  if abs(struct.unpack('>f',raw[2:6])[0]-50)<.01:break
  time.sleep(.2)
 else:raise AssertionError('Sensor did not reach S7 memory')
 pressure=struct.unpack('>f',raw[6:10])[0]
 assert 0 < pressure < .5, pressure
 s.db_write(1,0,bytearray([1]))
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  feedback=bool(s.db_read(1,1,1)[0]&1)
  if feedback:break
  time.sleep(.2)
 else:raise AssertionError('Actuator feedback did not reach protocol memories')
 print(f'Real S7: sensor 50, tank pressure {pressure:.3f} kPa; command and feedback OK')
finally:s.disconnect()
'''
        subprocess.run(['docker','run','--rm','--network',network,'--entrypoint','python',args.s7_image,'-c',probe],check=True)
        modbus_probe=r'''import time
from pymodbus.client import ModbusTcpClient
m=ModbusTcpClient('172.30.245.11',port=502);assert m.connect()
try:
 response=m.read_input_registers(0,count=1,device_id=1)
 assert not response.isError() and response.registers[0]==500
 pressure=m.read_input_registers(1,count=1,device_id=1)
 assert not pressure.isError() and 0 < pressure.registers[0] < 25, pressure
 assert not m.write_coil(0,True,device_id=1).isError()
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  response=m.read_coils(1,count=1,device_id=1)
  if not response.isError() and response.bits[0]:break
  time.sleep(.2)
 else:raise AssertionError('Motor feedback did not reach Modbus memory')
 print(f'Real Modbus: register 500, tank pressure {pressure.registers[0]} scaled; command and feedback OK')
finally:m.close()
'''
        subprocess.run(['docker','run','--rm','--network',network,'--entrypoint','python',args.modbus_image,'-c',modbus_probe],check=True)
        call(root+'/field/v/control','POST',{'fault':True})
        call(root+'/plcs/plc/write','POST',{'address':'DB1.DBX0.0','value':False})
        time.sleep(1.5)
        valve=next(o for o in call(root+'/field/values')['objects'] if o['object_id']=='v')
        assert valve['fault'] and valve['state'] is True and valve['command'] is False
        call(root+'/field/v/control','POST',{'fault':False});time.sleep(.5)
        valve=next(o for o in call(root+'/field/values')['objects'] if o['object_id']=='v')
        assert valve['state'] is False
        call(root+'/field/s/control','POST',{'override':72})
        time.sleep(.5)
        sensor=next(v for v in call(root+'/plcs/plc/values')['values'] if v['address']=='DB1.DBD2')
        assert abs(sensor['value']-72)<.01
        try:call(root+'/plcs/plc/write','POST',{'address':'DB1.DBX1.0','value':True})
        except urllib.error.HTTPError as error:assert error.code==403
        else:raise AssertionError('Field-owned input was writable from console')
        ids=subprocess.check_output(['docker','ps','-q','--filter',f'label=anvil.scenario_id={sid}']).decode().split()
        containers=json.loads(subprocess.check_output(['docker','inspect',*ids]))
        assert len(containers)==3
        assert all(not c['HostConfig']['PortBindings'] for c in containers)
        plc=next(c['Id'] for c in containers if c['Config']['Labels']['anvil.node_id']=='plc')
        subprocess.run(['docker','stop','-t','3',plc],check=True,stdout=subprocess.DEVNULL)
        time.sleep(2.3)
        status=call(root+'/field/values')
        assert next(c for c in status['connections'] if c['connection_id']=='order')['quality']=='stale'
        subprocess.run(['docker','start',plc],check=True,stdout=subprocess.DEVNULL)
        for _ in range(30):
            if next(c for c in call(root+'/field/values')['connections'] if c['connection_id']=='order')['quality']=='good':break
            time.sleep(.2)
        else:raise AssertionError('Field wiring did not reconnect')
        field_container=next(c['Id'] for c in containers if c['Config']['Labels']['anvil.node_id']=='__anvil_field__')
        subprocess.run(['docker','stop','-t','3',field_container],check=True,stdout=subprocess.DEVNULL)
        time.sleep(2.3)
        sensor=next(v for v in call(root+'/plcs/plc/values')['values'] if v['address']=='DB1.DBD2')
        assert abs(sensor['value']-72)<.01 and sensor['communication']['quality']=='stale'
        subprocess.run(['docker','start',field_container],check=True,stdout=subprocess.DEVNULL)
        for _ in range(30):
            sensor=next(v for v in call(root+'/plcs/plc/values')['values'] if v['address']=='DB1.DBD2')
            if sensor['communication']['quality']=='good':break
            time.sleep(.2)
        else:raise AssertionError('PLC did not recover after field runtime restart')
        print('Field-only factory, fault/restore, override, input permissions, no host ports and reconnection OK')
    finally:
        call(root+'/stop','POST');call(root,'DELETE')


if __name__=='__main__':main()
