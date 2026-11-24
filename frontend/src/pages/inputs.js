import {feedback, field} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

function withdrawForm(refresh) {
  const message = feedback();
  const submit = button('撤回票据', {type:'submit', class:'primary', 'data-testid':'withdraw-submit'});
  return el('form', {class:'fields', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    const result = await workspace.withdraw({batch_id:data.get('batch_id'),
      document_id:data.get('document_id'), reason:data.get('reason')});
    message.textContent = result.created
      ? `已撤回，${result.affected_runs.length} 份含该票的报告已停用并按原门槛重算。`
      : '该票据此前已撤回，认定不变。';
    await refresh();
  })}, field({key:'batch_id', label:'扫描批次', type:'string', max_length:300}),
    field({key:'document_id', label:'票据编号', type:'string', max_length:300}),
    field({key:'reason', label:'撤回理由', type:'string', required:false, max_length:500}), submit, message);
}

export async function inputs(route, refresh) {
  const {spec,datasets} = workspace;
  const selected = datasets.find(item => item.id === route.id) || datasets[0];
  const error = feedback();
  const sample = button('下载示例 CSV', {class:'quiet',onClick:event=>handler(sample,error,()=>workspace.download('/template',spec.sample_name))(event)});
  const detail = selected && await workspace.getDataset(selected.id,route.offset);
  const withdrawn = await workspace.withdrawals();
  const marks = new Set(withdrawn.map(item => `${item.batch_id}|${item.document_id}`));
  const columns = [...spec.fields.map(field => ({key:field.key,label:field.label})),
    {label:'状态', render:row => marks.has(`${row.batch_id}|${row.document_id}`) ? pill('已撤回','returned') : '计入'}];
  const preview = detail ? panel('批次明细', el('div',{class:'actions'},
    el('label',{for:'dataset-id',class:'sr'}, '选择数据批次'),
    el('select',{id:'dataset-id','aria-label':'选择数据批次',onChange:event=>{location.hash=`#inputs/${event.target.value}`;}},
      datasets.map(item=>el('option',{value:item.id,selected:item.id === selected.id}, item.name))),
    link('使用此批次运行',`#run/${detail.id}`,{class:'quiet'})),
    p(`${detail.filename} · ${detail.row_count} 行 · ${date(detail.created_at)}`,'filename'),
    table(columns,detail.rows,{caption:'批次明细',pageSize:16}),
    el('div',{class:'pagination'},
      route.offset > 0 && link('上一批明细',`#inputs/${detail.id}/${Math.max(0,route.offset-16)}`,{class:'quiet'}),
      p(`${detail.rows.length ? route.offset+1 : route.offset}–${route.offset+detail.rows.length} / ${detail.row_count}`),
      route.offset+16 < detail.row_count && link('下一批明细',`#inputs/${detail.id}/${route.offset+16}`,{class:'quiet'})))
    : empty('没有可用的批次。请导入示例或业务 CSV。');
  const submit = button('校验并导入', {type:'submit',class:'primary'});
  const upload = session.canWrite && el('form',{class:'fields',onSubmit:handler(submit,error,async event=>{
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const item = await workspace.upload({name:data.get('batch_name'),filename:file.name,csv_text:await file.text()});
    const target = `#inputs/${item.id}`;
    if (location.hash === target) await refresh();
    else location.hash = target;
  })},field({key:'batch_name',label:'批次名称',type:'string',max_length:120}),
    el('label',{class:'field',for:'source'}, el('span',{},'业务 CSV'),el('input',{id:'source',type:'file',name:'source',accept:'.csv,text/csv',required:true})), submit);
  const withdrawnList = table([
    {key:'batch_id',label:'扫描批次'}, {key:'document_id',label:'票据编号'},
    {label:'理由',render:row=>row.reason || '—'}, {key:'actor',label:'操作者'},
    {label:'时间',render:row=>date(row.created_at)},
  ], withdrawn, {caption:'已撤回票据', empty:'暂无撤回票据。'});
  return el('div',{class:'two-col'},el('div',{class:'wide-panel'},preview),el('aside',{class:'sidebar-panel'},
    panel('数据导入',p('导入后保留原批次和校验指纹，可用同一份数据复跑。','subtle'),sample,upload,error),
    panel('票据撤回',p('财务按扫描批次和票据编号撤回误入验收的票据。含该票的报告立即停用并按原门槛重算，旧文件再次导入也不会把撤回票算回。','subtle'),
      session.canReview ? withdrawForm(refresh) : p('撤回由复核主管操作。','subtle'), withdrawnList),
    panel('字段说明',el('dl',{class:'dictionary'},spec.fields.map(item=>el('div',{},el('dt',{},item.label),
      el('dd',{},item.key,item.required === false ? ' · 可空' : ' · 必填',item.options ? ` · ${item.options.join(' / ')}` : '')))))));
}
