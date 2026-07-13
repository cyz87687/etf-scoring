#!/usr/bin/env python3
"""V6 HTML 报告生成 (模板化，避免 f-string 大段 CSS 转义)"""
import json
import os

GRADE_CSS = {
    "优质配置": ("grade-excellent", "🟢", "#27ae60"),
    "中性偏多": ("grade-bullish", "🔵", "#2980b9"),
    "均衡观望": ("grade-neutral", "🟡", "#f39c12"),
    "谨慎规避": ("grade-cautious", "🟠", "#e67e22"),
    "坚决规避": ("🔴", "grade-avoid", "#e74c3c"),
}
ORDER = ["优质配置", "中性偏多", "均衡观望", "谨慎规避", "坚决规避"]


def grade_css(g):
    return GRADE_CSS.get(g, ("grade-neutral", "⚪", "#999"))[0]

def grade_emoji(g):
    return GRADE_CSS.get(g, ("grade-neutral", "⚪", "#999"))[1]

def grade_color(g):
    return GRADE_CSS.get(g, ("grade-neutral", "⚪", "#999"))[2]


def conf_badge(c):
    if c >= 0.6:
        return f'<span class="conf conf-high">高 {c:.0%}</span>'
    if c >= 0.35:
        return f'<span class="conf conf-mid">中 {c:.0%}</span>'
    return f'<span class="conf conf-low">低 {c:.0%}</span>'


def gen_reason(it):
    r = []
    if it["quality"] >= 7 and it["pe"] and it["pe"] < 25:
        r.append(f"低估(PE={it['pe']:.1f})")
    elif it["q_pos"] is not None and it["q_pos"] < 30 and it["q_cov"] >= 0.5:
        r.append(f"价格处低位({it['q_pos']:.0f}%分位)")
    if it["mom60"] is not None and it["mom60"] >= 20:
        r.append(f"60日+{it['mom60']:.0f}%")
    elif it["mom60"] is not None and it["mom60"] <= -10:
        r.append(f"60日{it['mom60']:.0f}%")
    if it["rsi14"] is not None and it["rsi14"] > 70:
        r.append(f"RSI超买{it['rsi14']:.0f}")
    if it["etf_flow_yuan"] and abs(it["etf_flow_yuan"]) > 1e7:
        r.append(f"ETF净{'申' if it['etf_flow_yuan'] > 0 else '赎'}{it['etf_flow_yuan']/1e8:.1f}亿")
    if it["sec_chg"] and it["sec_chg"] >= 2:
        r.append(f"板块+{it['sec_chg']:.1f}%")
    if not r:
        r.append("综合表现均衡")
    return " | ".join(r[:3])


CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; background:#f5f6fa; color:#2c3e50; }
.header { background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460); color:white; padding:28px 40px; text-align:center; }
.header h1 { font-size:23px; margin-bottom:6px; letter-spacing:1px; }
.header .sub { font-size:12px; opacity:.82; margin-bottom:10px; }
.tabs { display:flex; gap:4px; padding:14px 24px; background:white; border-bottom:1px solid #eee; flex-wrap:wrap; }
.tab { padding:8px 18px; border-radius:20px; cursor:pointer; font-size:13px; border:1px solid #ddd; background:white; transition:.2s; }
.tab:hover { background:#eef2ff; }
.tab.active { background:#0f3460; color:white; border-color:#0f3460; }
.content { max-width:1440px; margin:20px auto; padding:0 20px; }
.panel { display:none; } .panel.active { display:block; }
.summary-cards { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
.summary-card { background:white; border-radius:12px; padding:16px 20px; flex:1; min-width:150px; box-shadow:0 2px 8px rgba(0,0,0,.06); text-align:center; }
.summary-card .label { font-size:11px; color:#999; margin-bottom:4px; }
.summary-card .value { font-size:24px; font-weight:700; }
.summary-card .sub { font-size:11px; color:#888; margin-top:3px; }
.table-container { background:white; border-radius:12px; padding:18px; box-shadow:0 2px 8px rgba(0,0,0,.06); overflow-x:auto; margin-bottom:18px; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { background:#f8f9fa; padding:9px 6px; text-align:center; font-weight:600; color:#555; border-bottom:2px solid #e9ecef; white-space:nowrap; position:sticky; top:0; }
td { padding:7px 6px; text-align:center; border-bottom:1px solid #f0f0f0; }
tr:hover { background:#f8faff; }
.score-cell { font-weight:600; cursor:pointer; } .score-cell:hover { text-decoration:underline; }
.grade-tag { display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:14px; font-size:11px; font-weight:700; color:white; white-space:nowrap; }
.grade-excellent{background:linear-gradient(135deg,#27ae60,#2ecc71);} .grade-bullish{background:linear-gradient(135deg,#2980b9,#3498db);}
.grade-neutral{background:linear-gradient(135deg,#f39c12,#f1c40f);} .grade-cautious{background:linear-gradient(135deg,#e67e22,#f39c12);}
.grade-avoid{background:linear-gradient(135deg,#e74c3c,#c0392b);}
.red{color:#e74c3c;} .green{color:#27ae60;} .name-cell{text-align:left;font-weight:600;white-space:nowrap;}
.code-sub{font-size:10px;color:#999;font-weight:400;margin-top:2px;line-height:1.2;}
.proxy-tag{display:inline-block;font-size:9px;color:#b8860b;background:#fff3cd;border:1px solid #ffe08a;border-radius:4px;padding:0 3px;margin-left:3px;vertical-align:middle;font-weight:600;}
.reason{font-size:10px;color:#888;text-align:left;max-width:210px;white-space:normal;line-height:1.4;}
.conf{display:inline-block;padding:1px 7px;border-radius:8px;font-size:10px;font-weight:700;}
.conf-high{background:#d4edda;color:#155724;} .conf-mid{background:#fff3cd;color:#856404;} .conf-low{background:#f8d7da;color:#721c24;}
.src-tag{display:inline-block;padding:1px 6px;border-radius:6px;font-size:9px;font-weight:600;}
.src-A{background:#d4edda;color:#155724;} .src-B{background:#d1ecf1;color:#0c5460;} .src-C{background:#fff3cd;color:#856404;} .src-X{background:#f8d7da;color:#721c24;}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:999;justify-content:center;align-items:center;}
.modal-overlay.show{display:flex;}
.modal{background:white;border-radius:16px;padding:26px;max-width:640px;width:92%;max-height:84vh;overflow-y:auto;box-shadow:0 10px 40px rgba(0,0,0,.2);}
.modal h3{font-size:18px;margin-bottom:4px;} .modal .subtitle{font-size:12px;color:#888;margin-bottom:14px;}
.modal .score-big{font-size:42px;font-weight:700;margin:6px 0;}
.modal .item{padding:7px 0;border-bottom:1px solid #f5f5f5;font-size:12px;line-height:1.7;}
.modal .item:last-child{border-bottom:none;}
.modal-close{float:right;cursor:pointer;font-size:22px;color:#999;background:none;border:none;}
.modal-close:hover{color:#333;}
.method-box{background:white;border-radius:12px;padding:22px;box-shadow:0 2px 8px rgba(0,0,0,.06);margin-bottom:18px;}
.method-box h3{font-size:16px;margin-bottom:12px;border-bottom:2px solid #0f3460;padding-bottom:8px;display:inline-block;}
.method-box td{font-size:11px;padding:6px 9px;} .method-box td:first-child{font-weight:600;text-align:left;}
.audit-box{background:linear-gradient(135deg,#fff3cd,#fff8e1);border-left:4px solid #f39c12;border-radius:10px;padding:14px 18px;margin:12px 0;font-size:12px;}
.audit-box h4{color:#856404;margin-bottom:6px;font-size:14px;} .audit-box ul{margin-left:18px;} .audit-box li{margin:3px 0;line-height:1.6;}
.bar-cell{display:flex;align-items:center;gap:4px;justify-content:center;} .bar{height:8px;border-radius:4px;min-width:2px;}
.bar-quality{background:linear-gradient(90deg,#27ae60,#2ecc71);} .bar-tech{background:linear-gradient(90deg,#2980b9,#3498db);}
.bar-news{background:linear-gradient(90deg,#f39c12,#f1c40f);} .bar-capital{background:linear-gradient(90deg,#8e44ad,#9b59b6);}
footer{text-align:center;padding:28px;color:#aaa;font-size:11px;}
.note{font-size:11px;color:#888;margin-top:8px;padding:0 8px;line-height:1.6;}
"""

TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>指数四维精简量化评分体系 V6</title><style>__CSS__</style></head>
<body>
<div class="header">
  <h1>指数四维精简量化评分体系 V6</h1>
  <div class="sub">对齐《指数四维精简量化评分体系（可落地·API适配）》 · 0-10分制 · 四维等权25% · 数据时间 __NOW__</div>
  <div class="badge-row">
    <span class="conf conf-high">真实因子: K线RSI/波动率/动量/价格分位</span>
    <span class="conf conf-mid">代理因子: PE/PB/PS/股息/板块资金</span>
    <span class="conf conf-low">缺失因子按覆盖度标记置信度</span>
  </div>
</div>
<div class="tabs">
  <div class="tab active" onclick="switchTab('ranking',event)">📊 综合排名</div>
  <div class="tab" onclick="switchTab('factors',event)">📈 因子拆解</div>
  <div class="tab" onclick="switchTab('audit',event)">🔍 数据溯源</div>
  <div class="tab" onclick="switchTab('method',event)">📐 方法论</div>
</div>
<div class="content">

<div class="panel active" id="panel-ranking">
<div class="summary-cards">
  <div class="summary-card"><div class="label">评级分布</div><div class="value" style="font-size:13px;line-height:2">__GRADE_DIST__</div></div>
  <div class="summary-card"><div class="label">平均模型分</div><div class="value">__AVG_TOTAL__</div><div class="sub">/10</div></div>
  <div class="summary-card"><div class="label">🏆 TOP1</div><div class="value" style="font-size:17px">__TOP1_NAME__</div><div class="sub"><span class="grade-tag __TOP1_CLS__" style="font-size:10px;padding:2px 8px">__TOP1_TOTAL__分 · __TOP1_GRADE__</span><br><span style="font-size:10px">排序分 __TOP1_ADJ__</span></div></div>
  <div class="summary-card"><div class="label">平均数据置信度</div><div class="value">__AVG_CONF__</div><div class="sub">高≥60% 中≥35%</div></div>
</div>
<div class="table-container"><table><thead><tr>
  <th>#</th><th>指数</th><th>质量<div style="font-size:9px;color:#27ae60">25%</div></th>
  <th>资金面<div style="font-size:9px;color:#8e44ad">25%</div></th><th>技术面<div style="font-size:9px;color:#2980b9">25%</div></th>
  <th>消息面<div style="font-size:9px;color:#f39c12">25%</div></th><th>总分</th><th>评级</th><th>置信度</th><th>核心理由</th>
</tr></thead><tbody>__ROWS__</tbody></table>
<div class="note">⚠️ 置信度=四维覆盖度均值。低置信度指数评分仅反映有限数据，仅供参考，不构成投资建议。评分基于样本内横截面相对排名。</div>
</div></div>

<div class="panel" id="panel-factors">
<div class="table-container"><table><thead><tr><th>#</th><th>指数</th>
  <th colspan="5" style="background:#e8f5e9">质量因子(0-10)</th>
  <th colspan="4" style="background:#f3e5f5">资金面因子(0-10)</th>
  <th colspan="4" style="background:#e3f2fd">技术面因子(0-10)</th>
  <th colspan="3" style="background:#fff8e1">消息面因子(0-10)</th></tr>
  <tr><th></th><th></th><th>PE</th><th>PB</th><th>PS</th><th>股息</th><th>价格分位</th>
  <th>主力</th><th>北向代理</th><th>融资代理</th><th>机构代理</th>
  <th>趋势</th><th>量价</th><th>RSI</th><th>波动率</th><th>景气</th><th>预期</th><th>舆情</th></tr></thead><tbody>__FACTOR_ROWS__</tbody></table></div>
<div class="table-container"><table><thead><tr><th>#</th><th>指数</th><th>收盘</th><th>日涨跌%</th><th>PE</th><th>PB</th><th>PS</th><th>股息%</th>
  <th>120日分位</th><th>60日动量%</th><th>RSI</th><th>年化波动%</th><th>量价比</th><th>换手%</th><th>ETF净流入亿</th><th>份额变%</th></tr></thead><tbody>__RAW_ROWS__</tbody></table></div>
<div class="note">子因子评分=样本内百分位/10（缺失因子不参与均值计算）。价格分位=收盘价在近120日区间的位置，越低越便宜，作为通用估值位置代理。</div>
</div>

<div class="panel" id="panel-audit">
<div class="audit-box"><h4>🔍 V6 数据溯源核查</h4><ul>
  <li><strong>评分体系</strong>: 对齐《指数四维精简量化评分体系（可落地·API适配）》</li>
  <li><strong>评分范围</strong>: 0-10分，四维等权25%</li>
  <li><strong>评级标准</strong>: 9-10优质配置 | 7-8.9中性偏多 | 5-6.9均衡观望 | 3-4.9谨慎规避 | 0-2.9坚决规避</li>
  <li><strong>数据时间</strong>: __NOW__ CST</li>
  <li><strong>数据源</strong>: 东方财富(估值/ETF资金流) + 腾讯财经(行情/K线) + 新浪(板块)；多源容错，失败自动降级</li>
  <li><strong>V6核心改进</strong>: ①配置统一(Excel单一来源) ②K线真实RSI/波动率/动量/价格分位 ③数据置信度(缺失不全给5分) ④剔除CSI指数免费数据缺口(诚实标记)</li>
</ul></div>
<div class="method-box"><h3>数据覆盖率统计（有真实数据 / __N__ 指数）</h3><table>
<tr><th>数据维度</th><th>有真实数据</th><th>覆盖率</th></tr>__COVERAGE_ROWS__</table></div>
<div class="audit-box" style="background:linear-gradient(135deg,#f8d7da,#fce4ec);border-left-color:#e74c3c">
<h4 style="color:#721c24">⚠️ 数据局限说明</h4><ul>
  <li><strong>CSI/港股指数</strong>: 免费API（腾讯/新浪）对中证、港股通类指数覆盖有限，这类指数技术面与估值可能缺失→置信度低，评分仅供参考</li>
  <li><strong>PB/PS/股息率</strong>: 依赖东方财富，沙箱/部分地区可能不可达；不可达时由"120日价格分位"代理估值位置</li>
  <li><strong>估值近似</strong>: 当某指数 PE/PB/PS/股息率缺失且东财不可达时，自动借用"名称相近指数"(如 港股创新药↔CS创新药)的同类估值，弹窗与原始表以"≈/近似"标注，仅供参考</li>
  <li><strong>ETF资金流/份额</strong>: 依赖东方财富ETF资金流向，缺失时该子因子按覆盖度降权</li>
  <li><strong>横截面相对排名</strong>: 评分反映当前样本内相对强弱，非历史绝对估值百分位</li>
</ul></div>
</div>

<div class="panel" id="panel-method">
<div class="method-box"><h3>📐 评分方法（V6）</h3>
<p style="font-size:12px;color:#888;margin-bottom:12px">四维等权25% · 子因子样本内百分位归一化至0-10 · 缺失因子不参与维度均值 · 指数综合分=四维度均值</p>
<table>
<tr><th>维度</th><th>子因子(0-10)</th><th>数据源/算法</th><th>代号</th></tr>
<tr><td rowspan="5">质量25%</td><td>PE百分位(反向)</td><td>东方财富/腾讯真实PE</td><td>q_pe</td></tr>
<tr><td>PB百分位(反向)</td><td>东方财富真实PB</td><td>q_pb</td></tr>
<tr><td>PS百分位(反向)</td><td>东方财富真实PS</td><td>q_ps</td></tr>
<tr><td>股息率百分位</td><td>东方财富真实股息</td><td>q_div</td></tr>
<tr><td>120日价格分位(反向)</td><td>腾讯K线收盘价区间位置(通用估值代理)</td><td>q_pos</td></tr>
<tr><td rowspan="4">资金25%</td><td>主力资金(板块5日净流入)</td><td>新浪/东财板块资金</td><td>c_inflow5d</td></tr>
<tr><td>北向代理(ETF净申赎)</td><td>东方财富ETF资金流</td><td>c_north</td></tr>
<tr><td>融资代理(指数换手率)</td><td>腾讯指数换手率</td><td>c_margin</td></tr>
<tr><td>机构代理(ETF份额变化)</td><td>东方财富ETF份额</td><td>c_inst</td></tr>
<tr><td rowspan="4">技术25%</td><td>趋势(60日动量)</td><td>腾讯K线真实动量</td><td>t_trend</td></tr>
<tr><td>量价(10日上涨/下跌量比)</td><td>腾讯K线真实量比</td><td>t_vp</td></tr>
<tr><td>RSI强弱(14)</td><td>腾讯K线真实RSI→绝对映射</td><td>t_rsi</td></tr>
<tr><td>波动率(20日年化)</td><td>腾讯K线真实波动率→绝对映射</td><td>t_vol</td></tr>
<tr><td rowspan="3">消息25%</td><td>景气度(板块涨跌)</td><td>新浪板块涨跌</td><td>n_policy</td></tr>
<tr><td>一致预期(ETF涨跌)</td><td>腾讯ETF涨跌</td><td>n_consensus</td></tr>
<tr><td>舆情(新闻计数)</td><td>腾讯/市场新闻关键词</td><td>n_sentiment</td></tr>
</table></div>
<div class="method-box"><h3>📊 评级标准</h3><table>
<tr><td style="color:#27ae60;font-weight:700">🟢 优质配置</td><td>9.0-10.0</td><td>四因子均衡优秀</td></tr>
<tr><td style="color:#2980b9;font-weight:700">🔵 中性偏多</td><td>7.0-8.9</td><td>多数因子偏多</td></tr>
<tr><td style="color:#f39c12;font-weight:700">🟡 均衡观望</td><td>5.0-6.9</td><td>因子均衡中性</td></tr>
<tr><td style="color:#e67e22;font-weight:700">🟠 谨慎规避</td><td>3.0-4.9</td><td>多因子偏弱</td></tr>
<tr><td style="color:#e74c3c;font-weight:700">🔴 坚决规避</td><td>0.0-2.9</td><td>全面弱势</td></tr>
</table></div>
</div>
</div>

<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
<div class="modal" id="modalContent" onclick="event.stopPropagation()"></div></div>
<footer>指数四维精简量化评分体系 V6 · 数据时间 __NOW__ · 仅供参考，不构成投资建议</footer>
<script>
const items = __ITEMS_JSON__;
function switchTab(name,e){document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.getElementById('panel-'+name).classList.add('active');if(e&&e.target)e.target.classList.add('active');}
function showDetail(idx){const s=items[idx];const gc={"优质配置":"#27ae60","中性偏多":"#2980b9","均衡观望":"#f39c12","谨慎规避":"#e67e22","坚决规避":"#e74c3c"}[s.grade]||"#999";const ge={"优质配置":"🟢","中性偏多":"🔵","均衡观望":"🟡","谨慎规避":"🟠","坚决规避":"🔴"}[s.grade]||"⚪";
let h='<button class="modal-close" onclick="closeModal(event)">✕</button>';
h+='<h3>'+s.name+'</h3>';h+='<div class="subtitle">'+s.code+' | '+s.sector+' | 置信度 '+Math.round(s.confidence*100)+'%</div>';
h+='<div class="score-big" style="color:'+gc+'">'+s.total.toFixed(1)+' <span style="font-size:16px">'+ge+' '+s.grade+'</span></div>';
h+='<div style="font-size:11px;color:#888">模型分 '+s.total.toFixed(1)+' · 置信度排序分 '+s.adj_total.toFixed(1)+' · 数据置信 '+(s.conf_grade||('覆盖'+Math.round(s.confidence*100)+'%'))+'</div>';
h+='<div style="margin:10px 0">';
h+='<div class="item"><strong style="color:#27ae60">质量 '+s.quality.toFixed(1)+'/10</strong>(覆盖'+Math.round(s.q_cov*100)+'%) — PE:'+s.q_pe.toFixed(1)+' PB:'+s.q_pb.toFixed(1)+' PS:'+s.q_ps.toFixed(1)+' 股息:'+s.q_div.toFixed(1)+' 价格分位:'+s.q_pos.toFixed(1)+'</div>';
h+='<div class="item"><strong style="color:#8e44ad">资金面 '+s.capital.toFixed(1)+'/10</strong>(覆盖'+Math.round(s.c_cov*100)+'%) — 主力:'+s.c_inflow5d.toFixed(1)+' 北向代理:'+s.c_north.toFixed(1)+' 融资代理:'+s.c_margin.toFixed(1)+' 机构代理:'+s.c_inst.toFixed(1)+'</div>';
h+='<div class="item"><strong style="color:#2980b9">技术面 '+s.technical.toFixed(1)+'/10</strong>(覆盖'+Math.round(s.t_cov*100)+'%) — 趋势:'+s.t_trend.toFixed(1)+' 量价:'+s.t_vp.toFixed(1)+' RSI:'+s.t_rsi.toFixed(1)+' 波动:'+s.t_vol.toFixed(1)+'</div>';
h+='<div class="item"><strong style="color:#f39c12">消息面 '+s.news.toFixed(1)+'/10</strong>(覆盖'+Math.round(s.n_cov*100)+'%) — 景气:'+s.n_policy.toFixed(1)+' 预期:'+s.n_consensus.toFixed(1)+' 舆情:'+s.n_sentiment.toFixed(1)+'</div>';
h+='</div><div style="margin-top:8px;font-size:11px;color:#888;border-top:1px solid #eee;padding-top:8px">';
h+='收盘:'+(s.close!=null?s.close.toFixed(2):'-')+' | 日涨跌:'+(s.change_pct>=0?'+':'')+s.change_pct.toFixed(2)+'%<br>';
h+='PE:'+(s.pe||'-')+(s.pe_proxy?'≈':'')+' | PB:'+(s.pb||'-')+(s.pb_proxy?'≈':'')+' | PS:'+(s.ps||'-')+(s.ps_proxy?'≈':'')+' | 股息:'+(s.div?s.div.toFixed(2)+'%':'-')+(s.div_proxy?'≈':'')+' | 120日分位:'+(s.pct120!=null?s.pct120.toFixed(0)+'%':'-')+'<br>';
h+='60日动量:'+(s.mom60!=null?s.mom60.toFixed(1)+'%':'-')+' | RSI:'+(s.rsi14!=null?s.rsi14.toFixed(0):'-')+' | 年化波动:'+(s.vol20!=null?s.vol20.toFixed(1)+'%':'-')+' | 量价比:'+(s.vp10!=null?s.vp10.toFixed(2):'-')+'<br>';
const fy=s.etf_flow_yuan/1e8;h+='ETF净流入:'+(s.etf_flow_yuan?(fy>=0?'+':'')+fy.toFixed(2)+'亿':'-')+' | 份额变:'+(s.shares_chg_ratio?s.shares_chg_ratio.toFixed(2)+'%':'-');
h+='</div>';document.getElementById('modalContent').innerHTML=h;document.getElementById('modalOverlay').classList.add('show');}
function closeModal(e){if(e.target===document.getElementById('modalOverlay')||e.target.classList.contains('modal-close')){document.getElementById('modalOverlay').classList.remove('show');}}
document.addEventListener('keydown',function(e){if(e.key==='Escape'){document.getElementById('modalOverlay').classList.remove('show');}});
</script></body></html>"""


def build_html(items, now, real_data, n):
    grade_counts = {g: sum(1 for s in items if s["grade"] == g) for g in ORDER}
    avg_total = round(sum(s["total"] for s in items) / n, 2)
    avg_conf = round(sum(s["confidence"] for s in items) / n, 2)

    gd = ""
    for g in ORDER:
        c = grade_counts.get(g, 0)
        if c:
            gd += f'<span class="grade-tag {grade_css(g)}" style="margin:2px">{grade_emoji(g)} {g} {c}</span> '

    rows = ""
    for s in items:
        gc = grade_color(s["grade"])
        flag = f' <span class="src-tag src-X">数据不足</span>' if s["confidence"] < 0.35 else ""
        rows += f'''<tr>
<td>{s['rank']}</td><td class="name-cell">{s['name']}{flag}<div class="code-sub">{s['code']}</div></td>
<td><span class="score-cell" onclick="showDetail({s['rank']-1})">{s['quality']:.1f}</span></td>
<td><span class="score-cell" onclick="showDetail({s['rank']-1})">{s['capital']:.1f}</span></td>
<td><span class="score-cell" onclick="showDetail({s['rank']-1})">{s['technical']:.1f}</span></td>
<td><span class="score-cell" onclick="showDetail({s['rank']-1})">{s['news']:.1f}</span></td>
<td style="font-weight:700;font-size:15px;color:{gc}">{s['total']:.1f}</td>
<td><span class="grade-tag {grade_css(s['grade'])}">{grade_emoji(s['grade'])} {s['grade']}</span></td>
<td>{conf_badge(s['confidence'])}<br><span style="font-size:9px;color:#999">排序{s['adj_total']:.1f}</span></td>
<td class="reason">{gen_reason(s)}</td></tr>'''

    frows = ""
    for s in items:
        frows += f'''<tr><td>{s['rank']}</td><td class="name-cell">{s['name']}<div class="code-sub">{s['code']}</div></td>
<td>{s['q_pe']:.1f}</td><td>{s['q_pb']:.1f}</td><td>{s['q_ps']:.1f}</td><td>{s['q_div']:.1f}</td><td>{s['q_pos']:.1f}</td>
<td>{s['c_inflow5d']:.1f}</td><td>{s['c_north']:.1f}</td><td>{s['c_margin']:.1f}</td><td>{s['c_inst']:.1f}</td>
<td>{s['t_trend']:.1f}</td><td>{s['t_vp']:.1f}</td><td>{s['t_rsi']:.1f}</td><td>{s['t_vol']:.1f}</td>
<td>{s['n_policy']:.1f}</td><td>{s['n_consensus']:.1f}</td><td>{s['n_sentiment']:.1f}</td></tr>'''

    rrows = ""
    for s in items:
        def fv(v, suf=""):
            return f'{v:.2f}{suf}' if isinstance(v, (int, float)) else '-'
        cp = s['change_pct']
        cp_str = (f"{cp:+.2f}%" if isinstance(cp, (int, float)) else '-')
        cp_cls = 'red' if (isinstance(cp, (int, float)) and cp < 0) else 'green'
        def val(v, proxy=False, suf=""):
            base = f'{v:.2f}{suf}' if isinstance(v, (int, float)) else '-'
            if proxy:
                base += '<span class="proxy-tag">近似</span>'
            return base
        rrows += f'''<tr><td>{s['rank']}</td><td class="name-cell">{s['name']}<div class="code-sub">{s['code']}</div></td>
<td>{fv(s['close'])}</td><td class="{cp_cls}">{cp_str}</td>
<td>{val(s['pe'], s.get('pe_proxy'))}</td><td>{val(s['pb'], s.get('pb_proxy'))}</td><td>{val(s['ps'], s.get('ps_proxy'))}</td><td>{val(s['div'], s.get('div_proxy'),'%')}</td>
<td>{fv(s['pct120'],'%')}</td><td>{fv(s['mom60'],'%')}</td><td>{fv(s['rsi14'])}</td>
<td>{fv(s['vol20'],'%')}</td><td>{fv(s['vp10'])}</td><td>{fv(s['idx_turnover'],'%')}</td>
<td>{fv(s['etf_flow_yuan']/1e8) if s['etf_flow_yuan'] else '-'}</td><td>{fv(s['shares_chg_ratio'],'%') if s['shares_chg_ratio'] else '-'}</td></tr>'''

    cov_rows = ""
    for k, v in real_data.items():
        pct = v / n * 100
        col = "#27ae60" if pct >= 60 else "#f39c12" if pct >= 30 else "#e74c3c"
        cov_rows += f'<tr><td>{k}</td><td>{v}/{n}</td><td style="color:{col};font-weight:700">{pct:.0f}%</td></tr>'

    top = items[0]
    html = (TPL.replace("__CSS__", CSS)
            .replace("__NOW__", now)
            .replace("__GRADE_DIST__", gd)
            .replace("__AVG_TOTAL__", str(avg_total))
            .replace("__TOP1_NAME__", top["name"])
            .replace("__TOP1_CLS__", grade_css(top["grade"]))
            .replace("__TOP1_TOTAL__", str(top["total"]))
            .replace("__TOP1_GRADE__", top["grade"])
            .replace("__TOP1_ADJ__", str(top["adj_total"]))
            .replace("__AVG_CONF__", f"{avg_conf:.0%}")
            .replace("__ROWS__", rows)
            .replace("__FACTOR_ROWS__", frows)
            .replace("__RAW_ROWS__", rrows)
            .replace("__COVERAGE_ROWS__", cov_rows)
            .replace("__N__", str(n))
            .replace("__ITEMS_JSON__", json.dumps(items, ensure_ascii=False)))
    return html


def save(items, now, real_data, out_dir):
    html = build_html(items, now, real_data, len(items))
    p = os.path.join(out_dir, "index_v5.html")
    with open(p, "w") as f:
        f.write(html)
    print(f"\n已生成HTML报告: {p} ({len(html)} 字符)")
    return p
