import { registerComponentType } from './registry.js?v=field-live-3';
registerComponentType('io_channel', {
  label: 'Canal de E/S', icon: 'IO', deviceTypes: ['plc-s7'],
  create: (i=1) => ({type:'io_channel',channel_id:`channel-${crypto.randomUUID()}`,name:`entrada_${i}`,db:1,area:'DB',direction:'input',data_type:'BOOL',unit:''}),
  fields: [
    {path:'name',label:'Nombre del canal',type:'text',full:true},
    {path:'db',label:'DB',type:'number',min:1},
    {path:'area',label:'Área S7',type:'select',options:[{value:'DB',label:'DB · bloque de datos'},{value:'M',label:'M · marcas'},{value:'I',label:'I · entradas'},{value:'Q',label:'Q · salidas'}]},
    {path:'byte_offset',label:'Byte · vacío = automático',type:'number',min:0},
    {path:'bit_offset',label:'Bit BOOL · 0–7',type:'number',min:0,max:7},
    {path:'direction',label:'Dirección',type:'select',options:[{value:'input',label:'Entrada de campo'},{value:'output',label:'Salida de mando (BOOL/REAL)'}]},
    {path:'data_type',label:'Tipo',type:'select',options:[{value:'BOOL',label:'BOOL'},{value:'REAL',label:'REAL'}]},
    {path:'unit',label:'Unidad',type:'text'},
    {path:'min_value',label:'Límite inferior de señal REAL',type:'number',step:'any'},
    {path:'max_value',label:'Límite superior de señal REAL',type:'number',step:'any'},
  ],
});
