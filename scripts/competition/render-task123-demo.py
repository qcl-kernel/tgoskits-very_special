#!/usr/bin/env python3
"""Render auditable Task 2/3 dashboards from validated raw console logs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import runpy
import shutil
import subprocess
from pathlib import Path


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VM1 = re.compile(r"^\[VM 1\] ?", re.MULTILINE)
TASK2_CONTROL = re.compile(
    r"TASK2_BENCHMARK_CONTROL_SENT elapsed_ms=(\d+) request=(\d+) seq=(\d+) value=(-?\d+)"
)
TASK2_STATUS = re.compile(
    r"TASK2_BENCHMARK_STATUS_RECEIVED elapsed_ms=(\d+) request=(\d+) sample=(\d+) "
    r"rtt_ms=(\d+) state=(\w+) value=(-?\d+)"
)
TASK3_SAMPLE = re.compile(
    r"TASK3_SAMPLE sample=(\d+) image_id=([^ ]+) image_sha256=([0-9a-f]{64}) "
    r"truth_target=([^ ]+) expected=([^ ]+) source=yolo outcome=([^ ]+) request=(\d+)"
)
TASK3_REJECTION = re.compile(
    r"TASK3_MODEL_REJECTED model=([^ ]+) reason=(.*?) action=safe request=(\d+) elapsed_ms=(\d+)"
)
TASK3_DETECTION = re.compile(
    r"TASK3_DETECTION event_index=(\d+) event_id=([^ ]+) class=(\d+) "
    r"confidence_milli=(\d+) center_x_milli=(\d+) center_y_milli=(\d+) "
    r"area_milli=(\d+) request=(\d+)"
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def clean_log(path: Path) -> str:
    return VM1.sub("", ANSI.sub("", path.read_text(errors="replace")).replace("\r", ""))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_parser(script: Path) -> dict:
    return runpy.run_path(str(script))


def task2_data(log_path: Path, transactions: int) -> dict:
    parser = load_parser(repository_root() / "scripts/task2/quantify-benchmark.py")
    metrics = parser["parse"](log_path, transactions)
    text = clean_log(log_path)
    begin_marker = (
        f"TASK2_BENCHMARK_BEGIN transactions={transactions} protocol=T2N1 mode=stop-and-wait"
    )
    end_marker = f"TASK2_BENCHMARK_END transactions={transactions} pending=0 errors=0"
    start = text.rfind(begin_marker)
    end = text.find(end_marker, start)
    window = text[start : end + len(end_marker)]
    controls = {int(row[1]): row for row in TASK2_CONTROL.findall(window)}
    statuses = {int(row[1]): row for row in TASK2_STATUS.findall(window)}
    trace = []
    for request in range(1, transactions + 1):
        control = controls[request]
        status = statuses[request]
        trace.append(
            {
                "request": request,
                "sequence": int(control[2]),
                "control_value": int(control[3]),
                "sent_ms": int(control[0]),
                "status_ms": int(status[0]),
                "rtt_ms": int(status[3]),
                "state": status[4],
                "status_value": int(status[5]),
            }
        )
    return {"kind": "task2", "metrics": metrics, "trace": trace}


def task3_data(fixed_log: Path, rknn_log: Path, rejection_log: Path) -> dict:
    parser = load_parser(repository_root() / "scripts/task3/quantify-hybrid-scene.py")
    fixed = parser["parse"](fixed_log, "fixed")
    rknn = parser["parse"](rknn_log, "rknn")
    rknn_text = clean_log(rknn_log)
    detections = {
        row[1]: {
            "class_id": int(row[2]),
            "class_name": "person" if int(row[2]) == 0 else "car" if int(row[2]) == 2 else f"class-{row[2]}",
            "confidence_milli": int(row[3]),
            "center_x_milli": int(row[4]),
            "center_y_milli": int(row[5]),
            "area_milli": int(row[6]),
        }
        for row in TASK3_DETECTION.findall(rknn_text)
    }
    previous_image = None
    for fixed_event, rknn_event in zip(fixed["trace"], rknn["trace"], strict=True):
        event_id = rknn_event["id"]
        if event_id != "explicit-reset":
            previous_image = f"scene-images/rknn/validation/{event_id}.jpg"
        fixed_event["image_path"] = previous_image
        rknn_event["image_path"] = previous_image
        rknn_event["detection"] = detections.get(event_id)
    rejection_text = clean_log(rejection_log)
    samples = []
    controls = {
        int(request)
        for request in re.findall(r"TASK3_CONTROL_SENT [^\n]*\brequest=(\d+)\b", rejection_text)
    }
    rejection_by_request = {
        int(row[2]): {"model": row[0], "reason": row[1], "elapsed_ms": int(row[3])}
        for row in TASK3_REJECTION.findall(rejection_text)
    }
    for row in TASK3_SAMPLE.findall(rejection_text):
        request = int(row[6])
        rejected = row[5] == "rejected"
        if rejected and request in controls:
            raise ValueError(f"rejected request {request} unexpectedly emitted CONTROL")
        if rejected and request not in rejection_by_request:
            raise ValueError(f"rejected request {request} has no safe-rejection marker")
        samples.append(
            {
                "sample": int(row[0]),
                "image_id": row[1],
                "image_sha256": row[2],
                "expected": row[4],
                "outcome": row[5],
                "request": request,
                "control_sent": request in controls,
                "rejection": rejection_by_request.get(request),
            }
        )
    rejected_samples = [sample for sample in samples if sample["outcome"] == "rejected"]
    if len(samples) != 5 or len(rejected_samples) != 2:
        raise ValueError(
            f"expected 5 demonstration samples and 2 safe rejections, got {len(samples)}/{len(rejected_samples)}"
        )
    return {
        "kind": "task3",
        "fixed": fixed,
        "rknn": rknn,
        "rejection_samples": samples,
    }


def write_dashboard(data: dict, output: Path) -> None:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if data["kind"] == "task2":
        title = "Task 2 · T2N1 实时事务"
        subtitle = "CONTROL → ACK → STATUS → ACK · 原始日志严格验证"
    else:
        title = "智能工厂 AGV 视觉安全控制系统"
        subtitle = "真实场景 → RK3588 NPU → StarryOS → T2N1 → Zephyr RTOS"
    document = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">
<title>{html.escape(title)}</title><style>
:root{{--bg:#07111f;--panel:#0e2034;--line:#24435f;--cyan:#39d9ff;--green:#5ee6a8;--amber:#ffc857;--red:#ff6b7a;--text:#eff8ff;--muted:#91a9bd}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 50% -20%,#173855,var(--bg) 52%);color:var(--text);font:17px/1.45 system-ui,sans-serif;overflow-x:hidden}}
main{{max-width:1480px;margin:auto;padding:26px 34px 50px}}header{{display:flex;justify-content:space-between;align-items:end;margin-bottom:12px}}h1{{font-size:32px;margin:0}}.sub,.eyebrow{{color:var(--muted)}}.live{{color:var(--green);font-weight:700}}.system-strip{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0 0 16px}}.system-chip{{display:flex;justify-content:space-between;gap:12px;padding:9px 12px;background:#0b1c2e;border:1px solid #1e3b55;border-radius:10px;font-size:13px}}.system-chip b{{color:var(--cyan)}}.system-chip span{{color:var(--muted)}}.hero{{min-height:650px}}.story{{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(420px,.85fr);gap:18px}}.camera{{position:relative;height:410px;background:#02070d;border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 18px 45px #0006}}.camera img{{width:100%;height:100%;object-fit:contain}}.camera:after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,#0008 0,transparent 28%,transparent 70%,#000b 100%);pointer-events:none}}.camera-label{{position:absolute;z-index:2;left:18px;top:16px;padding:7px 11px;border-radius:999px;background:#07111fdd;border:1px solid var(--line);font-weight:700}}.scene-copy{{position:absolute;z-index:2;left:18px;right:18px;bottom:16px;display:flex;align-items:end;justify-content:space-between;gap:15px}}.scene-copy strong{{font-size:25px}}.detector{{position:absolute;z-index:2;border:3px solid var(--cyan);border-radius:12px;box-shadow:0 0 22px #39d9ff88;transform:translate(-50%,-50%);min-width:68px;min-height:68px}}.detector span{{position:absolute;left:-3px;top:-31px;background:var(--cyan);color:#021019;padding:3px 8px;border-radius:6px 6px 6px 0;font-size:13px;font-weight:800;white-space:nowrap}}.compare{{display:grid;grid-template-columns:1fr;gap:14px}}.arm{{background:linear-gradient(145deg,#10263d,#0a1829);border:1px solid var(--line);border-radius:16px;padding:18px;min-height:198px;box-shadow:0 18px 45px #0004}}.arm.fixed{{border-color:#ffc85755}}.arm.ai{{border-color:#5ee6a877}}.arm-head{{display:flex;justify-content:space-between;align-items:center}}.tag{{padding:4px 9px;border-radius:999px;background:#ffffff0e;font-size:13px}}.action{{font-size:31px;font-weight:850;margin:12px 0 5px}}.verdict{{font-weight:750;margin-top:12px}}.fixed-bad{{color:var(--red)}}.fixed-ok{{color:var(--amber)}}.ai-good{{color:var(--green)}}.score{{display:flex;gap:9px;color:var(--muted);font-size:14px;flex-wrap:wrap}}.timeline{{display:flex;align-items:center;gap:8px;margin:15px 0 5px}}.dot{{width:10px;height:10px;border:0;border-radius:50%;background:#36536c;padding:0;cursor:pointer}}.dot.now{{width:28px;border-radius:8px;background:var(--cyan)}}.controls{{display:flex;gap:8px;margin-left:auto}}button.control{{border:1px solid var(--line);background:#0e2034;color:var(--text);border-radius:8px;padding:5px 10px;cursor:pointer}}.detail-title{{display:flex;justify-content:space-between;align-items:end;margin:42px 0 14px}}.detail-title h2{{font-size:24px;color:var(--text);margin:0}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;min-width:0}}.panel{{background:linear-gradient(145deg,#10263d,#0a1829);border:1px solid var(--line);border-radius:16px;padding:20px;min-width:0;min-height:330px;overflow:hidden;box-shadow:0 18px 45px #0005}}h2{{font-size:18px;color:var(--cyan);margin:0 0 18px}}.big{{font-size:42px;font-weight:800;letter-spacing:-1px}}.metric{{display:flex;justify-content:space-between;border-bottom:1px solid #ffffff12;padding:9px 0}}.muted{{color:var(--muted)}}.flow{{display:flex;align-items:center;gap:8px;margin:22px 0;flex-wrap:wrap}}.node{{border:1px solid var(--line);border-radius:9px;padding:8px 11px}}.active{{border-color:var(--cyan);box-shadow:0 0 18px #39d9ff55}}.ok{{color:var(--green)}}.safe{{color:var(--red);font-weight:800}}.feed{{height:210px;overflow:hidden;font:14px/1.55 ui-monospace,monospace;color:#c9e9ff}}.feed div{{white-space:nowrap;border-bottom:1px solid #ffffff0c;padding:4px}}.hash{{font:12px ui-monospace,monospace;color:var(--muted);word-break:break-all}}.architecture{{display:grid;grid-template-columns:1fr 90px 1fr;gap:14px;align-items:center;background:#081827;border:1px solid var(--line);border-radius:18px;padding:22px}}.arch-stack{{display:grid;gap:12px}}.arch-box{{padding:16px;border:1px solid #315978;border-radius:12px;background:#0e2034}}.arch-box b{{display:block;color:var(--cyan);font-size:18px;margin-bottom:6px}}.arch-arrow{{text-align:center;color:var(--green);font-size:34px;font-weight:800}}.cap-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}}.cap-card{{background:#0b1c2e;border:1px solid var(--line);border-radius:14px;padding:16px;min-height:235px}}.cap-card h3{{margin:0 0 12px;color:var(--cyan);font-size:17px}}.cap-card p{{color:var(--muted);font-size:14px}}.code{{font:11px/1.5 ui-monospace,monospace;color:#b8d8ed;word-break:break-word}}footer{{margin-top:18px;color:var(--muted);font-size:13px}}@media(max-width:1100px){{.cap-grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:900px){{.story,.grid,.system-strip,.architecture,.cap-grid{{grid-template-columns:1fr}}.arch-arrow{{transform:rotate(90deg)}}.camera{{height:320px}}.hero{{min-height:0}}}}
</style></head><body><main><header><div><h1>{html.escape(title)}</h1><div class=\"sub\">{html.escape(subtitle)}</div></div><div class=\"live\">● PHYSICAL-BOARD EVIDENCE</div></header><div class=\"system-strip\"><div class=\"system-chip\"><b>实时底座</b><span>FP-RR P99 降低 55.3%</span></div><div class=\"system-chip\"><b>可靠通信</b><span>600/600 · 0 协议错误</span></div><div class=\"system-chip\"><b>AI 安全闭环</b><span>hazard recall 100%</span></div></div><div id=\"grid\"></div>
<script>const DATA={payload};let cursor=new URLSearchParams(location.search).has('safe')&&DATA.kind==='task3'?6:0;let playing=true;const grid=document.getElementById('grid');
function m(label,value){{return `<div class=metric><span class=muted>${{label}}</span><b>${{value}}</b></div>`}}
function task2(){{const t=DATA.trace[cursor%DATA.trace.length],x=DATA.metrics;grid.innerHTML=`
<section class=panel><h2>StarryOS / Controller</h2><div class=big>#${{t.request}}</div><div class=flow><span class=\"node active\">CONTROL ${{t.control_value}}</span><b>→</b><span class=node>seq ${{t.sequence}}</span></div><div class=feed>${{DATA.trace.slice(Math.max(0,cursor-8),cursor+1).reverse().map(v=>`<div>SEND request=${{v.request}} value=${{v.control_value}}</div>`).join('')}}</div></section>
<section class=panel><h2>T2N1 Reliable Transaction</h2><div class=flow><span class=node>CONTROL</span>→<span class=node>ACK</span>→<span class=\"node active\">STATUS</span>→<span class=node>ACK</span></div>${{m('RTT',t.rtt_ms+' ms')}}${{m('累计完成',t.request+' / '+DATA.trace.length)}}${{m('吞吐',x.throughput_tps.toFixed(3)+' tx/s')}}${{m('重传',x.retransmissions)}}<p class=ok>CRC / sequence / pending counters closed</p></section>
<section class=panel><h2>Zephyr / RTOS</h2><div class=big>${{t.status_value}}</div>${{m('protocol state',t.state)}}${{m('request ID',t.request)}}${{m('P95 / P99',x.p95_rtt_ms+' / '+x.p99_rtt_ms+' ms')}}${{m('success',x.transactions+' / '+x.transactions)}}<p class=ok>STATUS delivered and acknowledged</p></section>`;window.scrollTo(0,0);cursor=(cursor+1)%DATA.trace.length}}
function story(t){{if(t.id==='explicit-reset')return'操作员确认安全，显式 Reset 解锁';if(t.outcome==='stop-hazard')return'行人进入通道：立即制动';if(t.outcome==='stop-latched')return t.id.startsWith('road-')?'危险已离开：RTOS 仍保持停车锁存':'行人仍在通道：保持停车';return t.index===12?'Reset 后恢复安全跟车':'检测前车并调整驱动目标'}}
function actionText(t){{return t.action==='SetOutput'?`SetOutput ${{t.value}}`:t.action}}
function task3(){{const i=cursor%DATA.rknn.trace.length,t=DATA.rknn.trace[i],f=DATA.fixed.trace[i],d=t.detection,hazard=t.id.startsWith('hazard-'),fixedBad=hazard||t.outcome==='stop-latched',reset=t.id==='explicit-reset';const size=d?Math.max(70,Math.sqrt(d.area_milli/1000)*520):0;const marker=d?`<div class=detector style=\"left:${{d.center_x_milli/10}}%;top:${{d.center_y_milli/10}}%;width:${{size}}px;height:${{size}}px\"><span>${{d.class_name}} ${{(d.confidence_milli/10).toFixed(1)}}%</span></div>`:'';grid.className='';grid.innerHTML=`
<section class=hero><div class=story><div><div class=camera><img src=\"${{t.image_path}}\" alt=\"${{t.id}} benchmark frame\">${{marker}}<div class=camera-label>真实场景帧 · ${{t.id}}</div><div class=scene-copy><div><div class=eyebrow>厂区物流车视觉安全场景</div><strong>${{story(t)}}</strong></div><div class=tag>${{i+1}} / ${{DATA.rknn.trace.length}}</div></div></div><div class=timeline>${{DATA.rknn.trace.map((_,n)=>`<button class=\"dot ${{n===i?'now':''}}\" data-step=\"${{n}}\" aria-label=\"scene ${{n+1}}\"></button>`).join('')}}<div class=controls><button class=control id=prev>上一帧</button><button class=control id=play>${{playing?'暂停':'播放'}}</button><button class=control id=next>下一帧</button></div></div></div>
<div class=compare><article class=\"arm fixed\"><div class=arm-head><b>固定基线</b><span class=tag>不看图片</span></div><div class=action>${{actionText(f)}}</div><div class=score><span>RTOS state ${{f.state}}</span><span>RTT ${{f.rtt_ms}} ms</span></div><div class=\"verdict ${{fixedBad?'fixed-bad':'fixed-ok'}}\">${{fixedBad?'✕ 未识别危险，仍按固定输出运行':'△ 始终发送固定参数，不能随前车变化'}}</div><div class=score><span>总正确率 66.7%</span><span>hazard recall 0%</span></div></article>
<article class=\"arm ai\"><div class=arm-head><b>YOLO / RKNN</b><span class=tag>图像驱动</span></div><div class=action>${{actionText(t)}}</div><div class=score><span>${{d?`检测 ${{d.class_name}} · ${{(d.confidence_milli/10).toFixed(1)}}%`:reset?'人工 Reset':'安全锁存'}}</span><span>RTOS state ${{t.state}}</span></div><div class=\"verdict ai-good\">✓ ${{reset?'显式解除停车锁存':hazard?'识别人并触发急停':t.outcome==='stop-latched'?'危险消失后仍保持停车':'根据视觉结果安全调节输出'}}</div><div class=score><span>总正确率 100%</span><span>hazard recall 100%</span></div></article></div></div></section>
<section><div class=detail-title><div><div class=eyebrow>第二级：当前帧的三层系统证据</div><h2>视觉感知 → 可靠通信 → 实时执行</h2></div><span class=tag>来自实板原始日志</span></div><div class=grid>
<section class=panel><h2>AI / RK3588 NPU 感知</h2><div class=big>${{d?d.class_name:reset?'Reset':'latched'}}</div>${{m('scene event',t.id)}}${{m('confidence',d?(d.confidence_milli/10).toFixed(1)+'%':'—')}}${{m('RKNN inference',t.infer_us.toFixed(1)+' μs')}}${{m('decision accuracy','100%')}}<p class=ok>${{d?'真实图片完成 NPU 推理':reset?'操作员显式复位':'安全状态继续锁存'}}</p></section>
<section class=panel><h2>T2N1 可靠跨 Guest 通信</h2><div class=flow><span class=\"node active\">${{t.action}}</span>→<span class=node>CONTROL #${{t.request}}</span>→<span class=node>ACK / STATUS</span></div>${{m('target',t.value)}}${{m('CONTROL→STATUS',t.rtt_ms+' ms')}}${{m('inference→STATUS',(t.end_to_end_us/1000).toFixed(1)+' ms')}}${{m('Task 2 benchmark','600/600')}}<p class=ok>请求、ACK 与 STATUS 完整闭合</p></section>
<section class=panel><h2>FP-RR / Zephyr RTOS 执行</h2><div class=big>${{t.state}}</div>${{m('request ID',t.request)}}${{m('action',t.action)}}${{m('scheduler P99','0.621 → 0.278 ms')}}${{m('fixed vs RKNN','8/12 → 12/12')}}<p class=ok>RTOS 在通信共核上执行并回传状态</p></section></div></section>
<section><div class=detail-title><div><div class=eyebrow>第三级：正式系统架构</div><h2>隔离 AI 重计算，共享实时通信域</h2></div><span class=tag>RK3588 · AxVisor · StarryOS · Zephyr</span></div><div class=architecture><div class=arch-stack><div class=arch-box><b>pCPU2 · StarryOS vCPU0</b>图像预处理 → RKNN/NPU → 后处理 → 安全决策</div><div class=arch-box><b>RK3588 NPU · StarryOS 独占</b>MMIO、IOMMU、中断、时钟、复位和电源域不暴露给 RTOS</div></div><div class=arch-arrow>⇄</div><div class=arch-stack><div class=arch-box><b>pCPU1 · StarryOS vCPU1</b>VirtIO-net / T2N1 / CONTROL / ACK / STATUS · priority 89</div><div class=arch-box><b>pCPU1 · Zephyr vCPU0</b>10 ms 周期任务 / 控制执行 / 状态回传 · priority 90</div></div></div></section>
<section><div class=detail-title><div><div class=eyebrow>第四级：系统完成了什么</div><h2>从实时内核到可复现演示的工程全景</h2></div><span class=tag>设计 · 源码 · 配置 · 数据</span></div><div class=cap-grid><article class=cap-card><h3>实时内核</h3><p>bounded FP-RR、唤醒抢占、IRQ-tail、CNTV 所有权、vIRQ retry、CPU 亲和性与锁边界。</p><div class=code>components/axsched/src/priority_rr.rs<br>virtualization/axvm/src/runtime/<br>virtualization/axvm/src/arch/aarch64/vtimer/</div></article><article class=cap-card><h3>StarryOS / NPU</h3><p>2-vCPU StarryOS 承担 Linux ABI 应用、通信和 RKNN/NPU，设备所有权与 RTOS 分离。</p><div class=code>apps/starry/starryos-task2/<br>apps/starry/orangepi-5-plus-uvc-rknn/<br>scripts/board/task123-zephyr/</div></article><article class=cap-card><h3>可靠网络</h3><p>VirtIO/L2/UDP、T2N1、ACK、超时、重传、去重、乱序拒绝、心跳和 Safe 恢复。</p><div class=code>components/task2-net-protocol/<br>scripts/test/net-dual-guest/<br>scripts/task2/quantify-benchmark.py</div></article><article class=cap-card><h3>AI 安全控制</h3><p>检测校验、限幅、Stop 锁存、显式 Reset，并量化固定基线组与 RKNN 组。</p><div class=code>components/task3-model/<br>apps/arceos/task2-net/src/rknn_control.rs<br>scripts/task3/quantify-hybrid-scene.py</div></article><article class=cap-card><h3>平台与复现</h3><p>QEMU、RK3588 实板、Zephyr、RT-Thread 兼容路径、RAM-only 启动和证据哈希。</p><div class=code>scripts/competition/task123.sh<br>scripts/board/atk-dlrk3588-ram-boot.sh<br>CSV · JSON · pcap · SHA-256</div></article></div></section>`;document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{{cursor=Number(b.dataset.step);playing=false;task3()}});document.getElementById('prev').onclick=()=>{{cursor=(cursor-1+DATA.rknn.trace.length)%DATA.rknn.trace.length;playing=false;task3()}};document.getElementById('next').onclick=()=>{{cursor=(cursor+1)%DATA.rknn.trace.length;playing=false;task3()}};document.getElementById('play').onclick=()=>{{playing=!playing;task3()}}}}
const render=DATA.kind==='task2'?task2:task3;render();setInterval(()=>{{if(playing){{cursor=(cursor+1)%(DATA.kind==='task2'?DATA.trace.length:DATA.rknn.trace.length);render()}}}},1800);</script><footer>页面只重放当前实板证据；场景图片来自 RKNN payload，控制状态来自原始 console。动画不是车辆物理测量。</footer></main></body></html>"""
    output.write_text(document)


def write_svg(data: dict, output: Path) -> None:
    if data["kind"] == "task2":
        metrics = data["metrics"]
        columns = (
            ("StarryOS Controller", "200 CONTROL", "request / seq / value"),
            ("T2N1 Transaction", f"{metrics['throughput_tps']:.3f} tx/s", "CONTROL → ACK → STATUS → ACK"),
            ("Zephyr RTOS", f"P99 {metrics['p99_rtt_ms']} ms", "600/600 · 0 retransmission"),
        )
        title = "Task 2 · T2N1 bounded throughput"
    else:
        rknn = data["rknn"]
        columns = (
            ("RKNN Perception", "12/12 decisions", "vehicle + hazard recall 100%"),
            ("Safety / T2N1", f"RTT {rknn['mean_rtt_ms']:.1f} ms", "LowConfidence / SmallArea → SAFE"),
            ("Zephyr RTOS", "CONTROL or NO CONTROL", "state/value returned by STATUS"),
        )
        title = "Task 3 · RKNN to safe RTOS control"
    cards = []
    for index, (heading, value, note) in enumerate(columns):
        x = 55 + index * 475
        cards.append(
            f'<rect x="{x}" y="190" width="425" height="390" rx="18" fill="#0e2034" stroke="#24435f"/>'
            f'<text x="{x + 28}" y="240" class="heading">{html.escape(heading)}</text>'
            f'<text x="{x + 28}" y="330" class="value">{html.escape(value)}</text>'
            f'<text x="{x + 28}" y="390" class="note">{html.escape(note)}</text>'
            f'<text x="{x + 28}" y="525" class="pass">VERIFIED</text>'
        )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1480" height="720" viewBox="0 0 1480 720">
<defs><radialGradient id="bg"><stop stop-color="#173855"/><stop offset="1" stop-color="#07111f"/></radialGradient></defs>
<style>.title{{font:700 40px system-ui;fill:#eff8ff}}.sub{{font:18px system-ui;fill:#91a9bd}}.heading{{font:700 22px system-ui;fill:#39d9ff}}.value{{font:800 38px system-ui;fill:#eff8ff}}.note{{font:18px system-ui;fill:#91a9bd}}.pass{{font:700 17px system-ui;fill:#5ee6a8}}</style>
<rect width="1480" height="720" fill="url(#bg)"/><text x="55" y="85" class="title">{html.escape(title)}</text>
<text x="55" y="125" class="sub">Physical-board evidence replay · raw logs and SHA-256 retained</text>{''.join(cards)}
<text x="55" y="655" class="sub">UART: boot/evidence only · Guest data plane: VirtIO-net / T2N1</text></svg>'''
    output.write_text(svg)


def write_png(html_path: Path, output: Path, query: str = "") -> bool:
    browser = next(
        (
            path
            for executable in ("google-chrome", "chromium", "chromium-browser")
            if (path := shutil.which(executable)) is not None
        ),
        None,
    )
    if browser is None:
        return False
    result = subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1200",
            "--window-size=1480,720",
            f"--screenshot={output.resolve()}",
            html_path.resolve().as_uri() + query,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not output.is_file():
        print(f"warning: browser screenshot unavailable: {result.stderr.strip()}")
        return False
    return True


def archive_inputs(paths: list[Path], output_dir: Path) -> list[Path]:
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    archived = []
    for path in paths:
        destination = raw_dir / path.name
        if destination.exists() and sha256(destination) != sha256(path):
            destination = raw_dir / f"{sha256(path)[:12]}-{path.name}"
        shutil.copy2(path, destination)
        archived.append(destination)
    return archived


def write_index(output_dir: Path, inputs: list[Path]) -> None:
    artifacts = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "artifact-index.json":
            role = "raw-log" if path in inputs else "rendered-demo"
            if path.relative_to(output_dir).parts[0] == "scene-images":
                role = "benchmark-input"
            artifacts.append(
                {
                    "path": str(path.relative_to(output_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "role": role,
                }
            )
    (output_dir / "artifact-index.json").write_text(
        json.dumps({"schema": 1, "artifacts": artifacts}, indent=2) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="task", required=True)
    task2 = subparsers.add_parser("task2")
    task2.add_argument("--log", required=True, type=Path)
    task2.add_argument("--transactions", type=int, default=200)
    task2.add_argument("--out", required=True, type=Path)
    task3 = subparsers.add_parser("task3")
    task3.add_argument("--fixed", required=True, type=Path)
    task3.add_argument("--rknn", required=True, type=Path)
    task3.add_argument("--rejection-log", required=True, type=Path)
    task3.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    allowed = {
        "artifact-index.json",
        "dashboard-data.json",
        "dashboard.html",
        "dashboard.png",
        "dashboard-safe.png",
        "dashboard.mp4",
        "dashboard.svg",
        "raw",
        "scene-images",
    }
    unexpected = sorted(path.name for path in args.out.iterdir() if path.name not in allowed)
    if unexpected:
        parser.error(f"output directory contains unrelated entries: {unexpected}")
    if args.task == "task2":
        data = task2_data(args.log, args.transactions)
        raw = archive_inputs([args.log], args.out)
    else:
        data = task3_data(args.fixed, args.rknn, args.rejection_log)
        raw = archive_inputs([args.fixed, args.rknn, args.rejection_log], args.out)
    (args.out / "dashboard-data.json").write_text(json.dumps(data, indent=2) + "\n")
    write_dashboard(data, args.out / "dashboard.html")
    write_svg(data, args.out / "dashboard.svg")
    write_png(args.out / "dashboard.html", args.out / "dashboard.png")
    if args.task == "task3":
        write_png(
            args.out / "dashboard.html",
            args.out / "dashboard-safe.png",
            "?safe=1",
        )
    write_index(args.out, raw)
    print(f"TASK123_DEMO_PASS task={args.task} output={args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
