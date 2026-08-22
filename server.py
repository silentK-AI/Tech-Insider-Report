#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
科技内参 - 行情代理服务器
==========================
本地代理东方财富实时行情 / 快讯 / 分时数据接口，解决浏览器直接调用第三方接口的跨域(CORS)限制，
并在服务端完成板块聚合与交易温度计算。

启动:  python3 server.py [port]   (默认 8080)
接口:
  GET /              -> 静态站点 (index.html)
  GET /api/overview  -> 指数行情 + 板块聚合行情 + 温度
  GET /api/news      -> 按科技赛道分类的 7x24 快讯
  GET /api/trends    -> 各指数当日分时序列 (用于迷你走势图)
  GET /api/global    -> 美股核心科技标的行情 (18只, 覆盖8赛道)
  人物表态不单独成板块: 命中"人名+表态词"的快讯打 peopleNames 标签, 前端以人名徽标展示
"""
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
# 监听地址: 默认仅本机 (安全)。云服务器部署如需不经 Nginx 直接对外暴露,
# 可设环境变量 HOST=0.0.0.0 (如: HOST=0.0.0.0 python3 server.py 8080)
HOST = os.environ.get("HOST", "127.0.0.1")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# ============ 数据源配置 ============
# 指数 (secid, 显示名, 内部id)
INDEX_SECIDS = [
    ("1.000001", "上证指数", "sh"),
    ("0.399001", "深证成指", "sz"),
    ("0.399006", "创业板指", "cyb"),
    ("1.000688", "科创50", "kc50"),
    ("1.000685", "科创芯片", "tech"),
]

# 科技赛道及真实成分股 (secid, 股票名)
SECTORS = [
    {"id": "semi_chip", "name": "半导体芯片", "color": "#1890ff",
     "stocks": [("1.688981", "中芯国际"), ("1.603501", "韦尔股份"), ("1.603986", "兆易创新"),
                ("0.300782", "卓胜微"), ("1.688008", "澜起科技"), ("0.300661", "圣邦股份"),
                ("1.688347", "华虹公司")]},
    {"id": "semi_eq", "name": "半导体设备", "color": "#2f54eb",
     "stocks": [("0.002371", "北方华创"), ("1.688012", "中微公司"), ("1.688072", "拓荆科技"),
                ("1.688120", "华海清科"), ("1.688082", "盛美上海"), ("1.688037", "芯源微"),
                ("0.300567", "精测电子"), ("1.600641", "万业企业")]},
    {"id": "gpu", "name": "GPU芯片", "color": "#722ed1",
     "stocks": [("0.300474", "景嘉微"), ("1.688256", "寒武纪"), ("1.688041", "海光信息"),
                ("1.688047", "龙芯中科"), ("1.688521", "芯原股份"), ("0.002729", "好利科技")]},
    {"id": "storage", "name": "存储芯片", "color": "#13c2c2",
     "stocks": [("1.603986", "兆易创新"), ("0.300223", "北京君正"), ("1.688008", "澜起科技"),
                ("0.301308", "江波龙"), ("1.688525", "佰维存储"), ("0.001309", "德明利"),
                ("0.300475", "香农芯创"), ("0.000021", "深科技")]},
    {"id": "optical", "name": "光通信", "color": "#52c41a",
     "stocks": [("0.300308", "中际旭创"), ("0.300502", "新易盛"), ("0.002281", "光迅科技"),
                ("0.300394", "天孚通信"), ("1.600487", "亨通光电"), ("1.600522", "中天科技"),
                ("0.300570", "太辰光")]},
    {"id": "robot", "name": "机器人", "color": "#fa8c16",
     "stocks": [("0.002747", "埃斯顿"), ("0.300124", "汇川技术"), ("1.688017", "绿的谐波"),
                ("1.603728", "鸣志电器"), ("0.002472", "双环传动"), ("0.300607", "拓斯达"),
                ("1.688686", "奥普特"), ("1.688160", "步科股份")]},
    {"id": "ai_model", "name": "AI大模型", "color": "#eb2f96",
     "stocks": [("0.002230", "科大讯飞"), ("0.300418", "昆仑万维"), ("1.601360", "三六零"),
                ("1.688327", "云从科技"), ("0.300229", "拓尔思"), ("0.000977", "浪潮信息"),
                ("1.603019", "中科曙光")]},
    {"id": "ai_app", "name": "AI应用", "color": "#f5222d",
     "stocks": [("1.688111", "金山办公"), ("0.300033", "同花顺"), ("0.300624", "万兴科技"),
                ("1.688095", "福昕软件"), ("0.300378", "鼎捷软件"), ("0.300253", "卫宁健康"),
                ("1.603108", "润达医疗")]},
]

# 快讯关键词 -> 赛道 (按匹配数归类)。中英混合: 海外资讯(华尔街见闻/厂商RSS)与A股快讯共用一套分类
SECTOR_KEYWORDS = {
    "semi_chip": ["半导体", "芯片", "晶圆", "中芯国际", "华虹", "封测", "台积电", "联电",
                  "格芯", "cis", "射频", "硅片", "制程", "晶圆厂", "芯片设计", "代工",
                  "tsmc", "intel", "英特尔", "qualcomm", "高通", "semiconductor", "semis",
                  "foundry", "chipmaker", "wafer", "fab", "globalfoundries", "格罗方德",
                  "umc", "tsmc 2nm", "先进制程"],
    "semi_eq": ["光刻机", "刻蚀", "薄膜沉积", "cmp", "半导体设备", "北方华创", "中微公司",
                "拓荆", "华海清科", "盛美", "离子注入", "涂胶显影", "量测设备", "上海微电子", "asml",
                "applied materials", "lam research", "tokyo electron", "lithography", "光刻",
                "etch", "semiconductor equipment", "荷兰阿斯麦", "kla", "科磊", "teradyne",
                "advantest", "爱德万", "泛林", "lam", "应用材料", "东京电子", "光刻机", "euv"],
    "gpu": ["gpu", "英伟达", "nvidia", "寒武纪", "海光信息", "景嘉微", "龙芯", "摩尔线程",
            "算力芯片", "ai芯片", "asic", "tpu", "芯原", "显卡", "cuda",
            "amd", "blackwell", "hopper", "rtx", "mi300", "mi350", "数据中心芯片",
            "datacenter gpu", "graphics card", "radeon", "instinct"],
    "storage": ["存储", "dram", "nand", "闪存", "内存", "ssd", "hbm", "长江存储", "长鑫",
                "江波龙", "佰维", "ddr5", "澜起", "存储器", "固态硬盘",
                "samsung", "三星", "sk hynix", "海力士", "micron", "美光", "sandisk", "闪迪",
                "kioxia", "铠侠", "memory chip", "memory", "flash", "western digital",
                "3d nand", "v-nand", "hbm4", "hbm3e", "ddr4", "内存价格", "存储涨价"],
    "optical": ["光模块", "光通信", "光迅", "旭创", "新易盛", "天孚", "亨通", "800g", "1.6t",
                "硅光", "cpo", "光器件", "光纤光缆", "海缆", "光芯片",
                "broadcom", "博通", "coherent", "lumentum", "marvell", "optical",
                "silicon photonics", "optical module", "dsp芯片", "arista", "ciena",
                "infinera", "光模块需求", "cpo光模块"],
    "robot": ["机器人", "人形", "减速器", "伺服", "丝杠", "埃斯顿", "汇川", "拓斯达",
              "绿的谐波", "optimus", "宇树", "具身智能", "灵巧手", "机器狗",
              "boston dynamics", "figure ai", "humanoid", "robotics", "tesla bot", "人形机器人",
              "agility robotics", "apptronik", "unitree", "机器人大模型"],
    "ai_model": ["大模型", "gpt", "大语言模型", "deepseek", "豆包", "通义", "文心", "星火",
                 "智谱", "openai", "anthropic", "claude", "gemini", "千问", "混元", "推理模型", "moe",
                 "llm", "foundation model", "llama", "chatgpt", "大模型竞赛", "mistral",
                 "xai", "grok", "多模态模型", "模型发布"],
    "ai_app": ["ai应用", "ai营销", "ai医疗", "ai教育", "ai办公", "aigc", "智能体", "agent",
               "金山办公", "万兴", "福昕", "同花顺", "卫宁", "药明", "ai+", "sora", "ai医生", "ai投顾",
               "copilot", "ai agent", "ai marketing", "ai health", "salesforce", "ai 医疗", "ai 营销",
               "adobe", "palantir", "snowflake", "datadog", "servicenow", "tempus", "c3.ai",
               "微软", "office copilot", "ai助手", "ai编程"],
}

# 资讯方向判断词库 (中英混合)
UP_WORDS = ["涨停", "大涨", "上涨", "增长", "突破", "中标", "签约", "订单", "超预期", "获批",
            "量产", "认证", "新高", "回购", "增持", "合作", "创新高", "涨价", "提价", "利好",
            "爆发", "加速", "受益", "放量", "供不应求", "扩产", "上调", "创纪录", "胜诉",
            "upgrade", "upgrades", "raise", "raises", "raised", "surge", "surges", "jump",
            "jumps", "record", "beat", "beats", "exceeds", "growth", "boost", "soar", "rally",
            "rises", "rise", "win", "wins", "expand", "expansion", "investment"]
DOWN_WORDS = ["跌停", "大跌", "下跌", "下滑", "亏损", "减持", "处罚", "调查", "制裁", "限制",
              "退市", "预警", "被查", "落空", "承压", "利空", "降价", "下调", "流出", "封板",
              "裁员", "召回", "诉讼", "解禁", "质押", "终止", "撤回", "下滑",
              "cut", "cuts", "cutting", "downgrade", "downgrades", "fall", "falls", "fell",
              "drop", "drops", "decline", "slump", "miss", "misses", "loss", "layoff", "layoffs",
              "restriction", "ban", "sanction", "sanctions", "warning", "fears"]

# 资讯重要性判断词库
HEAVY_WORDS = ["重大", "重磅", "禁令", "制裁", "全球", "万亿", "刷新纪录", "里程碑", "垄断",
               "首次", "白宫", "商务部", "OpenAI", "英伟达", "台积电", "国务院", "央行",
               "历史性", "创纪录", "突破性",
               "global", "milestone", "historic", "breakthrough", "billion", "trillion",
               "record high", "first-ever"]
IMPORTANT_WORDS = ["增长", "中标", "订单", "量产", "认证", "获批", "处罚", "调查", "突破",
                   "合作", "发布", "融资", "收购", "投资", "扩产", "涨价", "降价", "新品",
                   "launch", "launches", "announces", "announced", "unveils", "partnership",
                   "funding", "acquisition", "expansion", "deal", "earnings", "revenue", "report"]

# 海外机构研报关键词 -> 资讯标注为研报类
RESEARCH_WORDS = ["研报", "评级", "目标价", "摩根士丹利", "大摩", "高盛", "花旗", "瑞银", "美银",
                  "巴克莱", "摩根大通", "小摩", "瑞信", "野村", "德意志银行", "伯恩斯坦", "大行",
                  "摩根", "机构观点", "分析师",
                  "morgan stanley", "goldman", "citi", "ubs", "bofa", "barclays", "jpmorgan",
                  "deutsche bank", "analyst", "analysts", "price target", "rating", "upgrade",
                  "downgrade", "bernstein", "susquehanna", "keybanc", "mizuho", "research note"]

# 重要人物表态追踪 (科技/政策掌门人) -> 命中"人物名+表态词"的资讯打上人物标签
PEOPLE = [
    {"id": "musk",     "name": "马斯克",     "role": "特斯拉·xAI·SpaceX", "keys": ["马斯克", "musk", "elon"]},
    {"id": "trump",    "name": "特朗普",     "role": "美国政策",          "keys": ["特朗普", "川普", "trump"]},
    {"id": "altman",   "name": "奥特曼",     "role": "OpenAI CEO",        "keys": ["奥特曼", "altman"]},
    {"id": "huang",    "name": "黄仁勋",     "role": "英伟达 CEO",        "keys": ["黄仁勋", "jensen huang"]},
    {"id": "su",       "name": "苏姿丰",     "role": "AMD CEO",           "keys": ["苏姿丰", "lisa su"]},
    {"id": "zuckerberg", "name": "扎克伯格", "role": "Meta CEO",          "keys": ["扎克伯格", "zuckerberg"]},
    {"id": "pichai",   "name": "皮查伊",     "role": "谷歌 CEO",          "keys": ["皮查伊", "pichai"]},
    {"id": "nadella",  "name": "纳德拉",     "role": "微软 CEO",          "keys": ["纳德拉", "nadella"]},
    {"id": "amodei",   "name": "阿莫迪",     "role": "Anthropic CEO",     "keys": ["阿莫迪", "amodei"]},
]
# 表态词: 命中人物名 + 任一表态词 -> 判定为人物表态类资讯 (而非单纯提及)
SAY_WORDS = ["称", "表示", "说", "宣布", "发帖", "发文", "呼吁", "警告", "回应", "表态",
             "评论", "透露", "预计", "认为", "主张", "支持", "反对", "建议", "喊话", "抨击",
             "炮轰", "怒斥", "坚持", "否认", "承诺",
             "says", "said", "say", "tweet", "tweets", "tweeted", "posted", "warns",
             "warned", "calls", "called", "urges", "urged", "claims", "claimed",
             "blasts", "slams", "predicts", "predicts", "expects", "argues",
             "announces", "announced", "unveils"]


def match_people(title, content):
    """识别资讯涉及的重要人物表态。返回命中的人物 id 列表 (按 PEOPLE 顺序)"""
    text = (title + " " + content).lower()
    hits = []
    for p in PEOPLE:
        keys = [k.lower() for k in p["keys"]]
        if not any(k in text for k in keys):
            continue
        # 表态判定: 命中表态词, 或标题以 "人名+冒号" 开头 (如 "马斯克：星舰...")
        said = any(w in text for w in SAY_WORDS)
        if not said:
            for k in keys:
                if title.lower().startswith(k + "：") or title.lower().startswith(k + ":"):
                    said = True
                    break
        if said:
            hits.append(p["id"])
    return hits


def people_names(ids):
    """人物 id 列表 -> 姓名列表 (用于前端徽标展示)"""
    m = {p["id"]: p["name"] for p in PEOPLE}
    return [m[i] for i in ids if i in m]


# ============ HTTP 工具 ============
def http_get(url, referer="https://quote.eastmoney.com/", timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ============ 行情抓取 ============
def fetch_quotes(secids):
    """批量获取行情。secids: ["1.600519", ...] -> {secid: {...}}"""
    if not secids:
        return {}
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
           "&fields=f2,f3,f6,f8,f10,f12,f13,f14,f20,f62&secids=" + ",".join(secids))
    try:
        data = json.loads(http_get(url))
        out = {}
        for d in (data.get("data") or {}).get("diff") or []:
            secid = "{}.".format(d.get("f13", 1)) + str(d.get("f12", ""))
            out[secid] = {
                "code": str(d.get("f12", "")),
                "name": d.get("f14", ""),
                "price": d.get("f2"),
                "change": d.get("f3"),
                "amount": d.get("f6") or 0,
                "turnover": d.get("f8") or 0,
                "volratio": d.get("f10") or 1,
                "cap": d.get("f20") or 0,
                "flow": d.get("f62") or 0,
            }
        return out
    except Exception as e:
        print("[quotes] err:", e)
        return {}


def calc_temp(chg, turnover_rate, net_flow_yi, vol_ratio):
    """交易温度: 成交额/涨跌幅 + 换手率 + 主力净流入 + 量比 四个维度归一化加权"""
    nc = max(-1.0, min(1.0, chg / 5.0))                       # ±5% 涨跌 -> ±1
    nt = max(-1.0, min(1.0, (turnover_rate - 1.0) / 4.0))     # 换手率 1%~5% -> 0~1
    nf = max(-1.0, min(1.0, net_flow_yi / 25.0))              # 主力净流入 ±25亿 -> ±1
    nv = max(-1.0, min(1.0, (vol_ratio - 0.8) / 1.2))         # 量比 0.8~2.0 -> 0~1
    return round(0.35 * nc + 0.25 * nt + 0.25 * nf + 0.15 * nv, 2)


def fetch_indices(quotes):
    out = []
    for secid, name, kid in INDEX_SECIDS:
        d = quotes.get(secid)
        if not d or d.get("price") is None:
            continue
        out.append({"id": kid, "name": name, "code": d["code"],
                    "value": round(d["price"], 2), "change": round(d["change"] or 0, 2),
                    "amount": round((d["amount"] or 0) / 1e8, 1)})
    return out


def fetch_sectors(quotes):
    out = []
    for s in SECTORS:
        stocks = []
        valid = 0
        chg_sum = 0.0
        cap_sum = 0.0
        chg_w = 0.0
        amt_sum = 0.0
        tr_w = 0.0
        vr_w = 0.0
        flow_sum = 0.0
        for secid, fallback_name in s["stocks"]:
            d = quotes.get(secid)
            if not d or d.get("price") is None:
                continue
            cap = d["cap"] or 0
            stocks.append({"code": d["code"], "name": d["name"] or fallback_name,
                           "price": round(d["price"], 2), "change": round(d["change"] or 0, 2),
                           "flow": round((d["flow"] or 0) / 1e8, 2),
                           "cap": round(cap / 1e8, 1)})
            valid += 1
            chg = d["change"] or 0
            chg_sum += chg
            amt = d["amount"] or 0
            amt_sum += amt
            if cap > 0:                     # 市值加权口径
                cap_sum += cap
                chg_w += chg * cap
                tr_w += (d["turnover"] or 0) * cap
                vr_w += (d["volratio"] or 1) * cap
            flow_sum += d["flow"] or 0
        if valid == 0:
            continue
        if cap_sum > 0:                     # 板块涨跌幅/换手率/量比 = 成分股按总市值加权
            chg = round(chg_w / cap_sum, 2)
            tr = round(tr_w / cap_sum, 2)
            vr = round(vr_w / cap_sum, 2)
        else:                               # 市值缺失时回退等权
            chg = round(chg_sum / valid, 2)
            tr = 0.0
            vr = 1.0
        nf = round(flow_sum / 1e8, 2)
        out.append({**s,
                    "change": chg,
                    "turnover": round(amt_sum / 1e8, 1),
                    "turnoverRate": tr,
                    "volRatio": vr,
                    "netFlow": nf,
                    "temp": calc_temp(chg, tr, nf, vr),
                    "stocks": stocks})
    return out


def load_overview():
    all_secids = [sec[0] for sec in INDEX_SECIDS]
    for s in SECTORS:
        all_secids.extend(c for c, _ in s["stocks"])
    quotes = fetch_quotes(list(dict.fromkeys(all_secids)))
    return {"indices": fetch_indices(quotes), "sectors": fetch_sectors(quotes)}


# ============ 快讯抓取与分类 ============
NEWS_POOL = {}          # id -> item, 去重
NEWS_POOL_LOCK = threading_lock = __import__("threading").Lock()


def _clean_title(t):
    t = re.sub(r"^【[^】]*】", "", t).strip()
    return t[:80]


def classify_sector(text):
    """中英混合分类。中文词子串匹配; 纯英文词按词边界 \b 匹配, 避免 ssd/gpu 等
    短词误命中 biosignals 这类复合词"""
    t = text.lower()
    best, best_cnt = None, 0
    for sid, words in SECTOR_KEYWORDS.items():
        cnt = 0
        for w in words:
            if re.fullmatch(r"[a-z0-9.+\-]+", w):
                if re.search(r"\b" + re.escape(w) + r"\b", t):
                    cnt += 1
            elif w in t:
                cnt += 1
        if cnt > best_cnt:
            best, best_cnt = sid, cnt
    return best


def classify_impact(text):
    ups = sum(1 for w in UP_WORDS if w in text)
    downs = sum(1 for w in DOWN_WORDS if w in text)
    if ups > downs:
        return "up"
    if downs > ups:
        return "down"
    return "neutral"


def classify_importance(text):
    if any(w in text for w in HEAVY_WORDS):
        return "heavy"
    if any(w in text for w in IMPORTANT_WORDS):
        return "important"
    return "normal"


def fmt_news_time(show_time):
    m = re.search(r"(\d{2}):(\d{2}):(\d{2})", show_time or "")
    if m:
        return m.group(0)
    return ""


def _clean_content(summary, digest, title):
    """快讯全文: 去掉与标题重复的【】前缀, 无正文时回退标题"""
    c = (summary or digest or title or "").strip()
    m = re.match(r"^【[^】]*】", c)
    if m:
        c = c[m.end():].strip()
    return c or title


def fetch_news(page_size=100):
    """国内资讯聚合: 东方财富 + 财联社 + 同花顺 + 新浪财经 7x24。
    各源条目统一入 NEWS_POOL (按 id 去重), 输出按时间倒序"""
    url = ("https://np-weblist.eastmoney.com/comm/web/getFastNewsList?"
           "client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize={}"
           "&req_trace=".format(page_size))
    items = []
    try:
        data = json.loads(http_get(url, referer="https://www.eastmoney.com/"))
        for n in (data.get("data") or {}).get("fastNewsList") or []:
            title = n.get("title") or ""
            summary = n.get("summary") or ""
            text = title + " " + summary
            sid = classify_sector(text)
            if sid is None:
                continue
            sector = next((s for s in SECTORS if s["id"] == sid), None)
            if sector is None:
                continue
            _peo = match_people(_clean_title(title), summary or "")
            items.append({
                "id": str(n.get("code") or ""),
                "region": "cn",
                "time": fmt_news_time(n.get("showTime") or ""),
                "fullTime": n.get("showTime") or "",
                "sector": sector["id"],
                "sectorName": sector["name"],
                "sectorColor": sector["color"],
                "title": _clean_title(title),
                "content": _clean_content(summary, n.get("digest"), title),
                "people": _peo, "peopleNames": people_names(_peo),
                "impact": classify_impact(text),
                "importance": classify_importance(text),
                "source": "东方财富",
            })
    except Exception as e:
        print("[news] err:", e)
    # 聚合其他国内源 (各自容错, 单源故障不影响整体)
    items.extend(fetch_cls())
    items.extend(fetch_ths())
    items.extend(fetch_sina_live())
    # 合并到全局池, 按时间倒序
    with NEWS_POOL_LOCK:
        for it in items:
            if it["id"] and it["id"] not in NEWS_POOL:
                NEWS_POOL[it["id"]] = it
        pool = sorted(NEWS_POOL.values(), key=lambda x: x["fullTime"], reverse=True)
        if len(pool) > 500:
            for old in pool[500:]:
                NEWS_POOL.pop(old["id"], None)
            pool = pool[:500]
    return pool


def fetch_cls():
    """财联社电报 7x24 (A 股公告/产业快讯, 质量高)。
    接口需签名: sign = md5(sha1(sorted_query_params)), 翻页用 last_time=上一页末条 ctime。
    拉 2 页 x 50 条覆盖更长窗口"""
    items = []
    last_time = ""
    for _page in range(2):
        params = ("app=CailianpressWeb&category=&last_time={}&os=web"
                  "&refresh_type=1&rn=50&sv=7.7.5".format(last_time))
        sign = hashlib.md5(hashlib.sha1(params.encode()).hexdigest().encode()).hexdigest()
        url = "https://www.cls.cn/v1/roll/get_roll_list?" + params + "&sign=" + sign
        try:
            data = json.loads(http_get(url, referer="https://www.cls.cn/telegraph"))
            roll = (data.get("data") or {}).get("roll_data") or []
            if not roll:
                break
            for it in roll:
                title = _clean_title(it.get("title") or "")
                content = re.sub(r"<[^>]+>", "", it.get("content") or it.get("brief") or "").strip()
                ts = it.get("ctime") or 0
                if not title or not ts:
                    continue
                full = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                nid = "cls:" + str(it.get("id") or "")
                item = _build_item(nid, full, title, content or title,
                                   it.get("shareurl") or "", "财联社", region="cn")
                if item:
                    items.append(item)
            last_time = str(roll[-1].get("ctime") or "")
            if not last_time:
                break
        except Exception as e:
            print("[cls] err:", e)
            break
    return items


def fetch_ths():
    """同花顺 7x24 快讯 (A股/港美股科技滚动)"""
    items = []
    url = ("https://news.10jqka.com.cn/tapp/news/push/stock/"
           "?page=1&tag=&track=website&pagesize=50")
    try:
        data = json.loads(http_get(url, referer="https://news.10jqka.com.cn/"))
        for it in (data.get("data") or {}).get("list") or []:
            title = _clean_title(it.get("title") or "")
            digest = (it.get("digest") or "").strip()
            try:
                ts = int(it.get("ctime") or 0)
            except (TypeError, ValueError):
                ts = 0
            if not title or not ts:
                continue
            full = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            nid = "ths:" + str(it.get("id") or "")
            item = _build_item(nid, full, title, digest or title,
                               it.get("url") or "", "同花顺", region="cn")
            if item:
                items.append(item)
    except Exception as e:
        print("[ths] err:", e)
    return items


def fetch_sina_live():
    """新浪财经 7x24 全球快讯直播 (zhibo_id=152)。
    rich_text 为【标题】正文 格式, 提取【】内为标题"""
    items = []
    url = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=100"
           "&zhibo_id=152&tag_id=0&dire=f&dpc=1")
    try:
        data = json.loads(http_get(url, referer="https://finance.sina.com.cn/"))
        feed = (((data.get("result") or {}).get("data") or {})
                .get("feed") or {}).get("list") or []
        for it in feed:
            text = re.sub(r"<[^>]+>", "", it.get("rich_text") or "").strip()
            full = (it.get("create_time") or "").strip()
            if not text or not full:
                continue
            m = re.match(r"^【([^】]{4,40})】(.*)$", text, re.S)
            if m:
                title, content = _clean_title(m.group(1)), m.group(2).strip()
            else:
                title, content = _clean_title(text[:60]), text
            nid = "sina:" + str(it.get("id") or "")
            item = _build_item(nid, full, title, content or title,
                               it.get("docurl") or "", "新浪财经", region="cn")
            if item:
                items.append(item)
    except Exception as e:
        print("[sina] err:", e)
    return items


# ============ 英文资讯翻译 ============
# MyMemory 免费翻译 API + 文件持久化缓存 (避免重复请求与重启丢失)
TRANS_CACHE_FILE = os.path.join(BASE_DIR, ".trans_cache.json")
_trans_cache = {}
_trans_lock = __import__("threading").Lock()
try:
    if os.path.exists(TRANS_CACHE_FILE):
        with open(TRANS_CACHE_FILE, "r", encoding="utf-8") as f:
            _trans_cache = json.load(f)
except Exception:
    _trans_cache = {}


def _save_trans_cache():
    try:
        with open(TRANS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_trans_cache, f, ensure_ascii=False)
    except Exception:
        pass


def _is_english(text):
    """标题以英文为主 (拉丁字母占比高 且 至少 3 个英文单词)"""
    letters = len(re.findall(r"[a-zA-Z]", text))
    words = len(re.findall(r"[a-zA-Z]{2,}", text))
    return letters >= 6 and words >= 3 and letters > len(text) * 0.35


# 翻译前保护的专有名词 (机器翻译易误译: Anthropic->人类, Coherent->连贯的 等)。
# 常见厂商名(三星/台积电/SK海力士/美光/英伟达等)不保护, MyMemory 能正确译成中文。
_PROTECT_NAMES = ("Anthropic", "Coherent", "Palantir", "xAI", "Grok", "SpaceX",
                  "Infinera", "Lumentum", "Daybreak", "Taalas", "Instinct")


def translate_en(text):
    """英文 -> 中文。MyMemory API + 内存/文件缓存。失败或非英文返回原文。
    专有名词先替换为占位符, 翻译后还原, 避免被误译"""
    if not _is_english(text) or len(text) > 450:
        return text
    if text in _trans_cache:
        return _trans_cache[text]
    # 保护专有名词 (zzzN 占位符经 MyMemory 可原样保留)
    subs = {}
    def _protect(m):
        i = len(subs)
        ph = "zzz{}".format(i)
        subs[ph] = m.group(0)
        return ph
    t2 = re.sub(r"\b(?:{})\b".format("|".join(re.escape(n) for n in _PROTECT_NAMES)),
                _protect, text, flags=re.IGNORECASE)
    try:
        url = ("https://api.mymemory.translated.net/get?q={}&langpair=en|zh-CN".format(
            urllib.parse.quote(t2)))
        data = json.loads(http_get(url, referer=""))
        zh = ((data.get("responseData") or {}).get("translatedText") or "").strip()
        if zh and data.get("responseStatus") == 200:
            for ph, name in subs.items():
                zh = zh.replace(ph, name)
            with _trans_lock:
                _trans_cache[text] = zh
                _save_trans_cache()
            return zh
    except Exception as e:
        print("[translate] err:", e)
    return text


# ============ 全球(美股)行情 ============
def fetch_us_market():
    """新浪美股行情 (gb_ 前缀, GBK 编码)。返回 [{code,name,price,change,chg,time}, ...]"""
    if not US_STOCKS:
        return []
    url = "https://hq.sinajs.cn/list=" + ",".join("gb_" + s["code"] for s in US_STOCKS)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", "ignore")
    except Exception as e:
        print("[us-market] err:", e)
        return []
    code_of = {s["code"]: s for s in US_STOCKS}
    out = []
    for m in re.finditer(r"hq_str_gb_(\w+)=\"([^\"]*)\"", raw):
        code, data = m.group(1), m.group(2)
        cfg = code_of.get(code)
        if not cfg or not data:
            continue
        f = data.split(",")
        if len(f) < 5 or not f[1]:
            continue
        try:
            price = float(f[1]); chg = float(f[2]); chg_amt = float(f[4])
        except ValueError:
            continue
        out.append({"code": code.upper(), "name": cfg["name"], "sector": cfg["sector"],
                    "price": round(price, 2), "change": round(chg, 2),
                    "chgAmt": round(chg_amt, 2),
                    "time": f[3][:16] if len(f) > 3 else ""})
    # 按 US_STOCKS 定义顺序返回 (稳定的板块分组展示顺序)
    order = {s["code"]: i for i, s in enumerate(US_STOCKS)}
    out.sort(key=lambda x: order.get(x["code"].lower(), 99))
    return out


# ============ 海外资讯抓取 ============
# 厂商官方 RSS 源 (一手信息)。覆盖 存储(三星/SK海力士) GPU(英伟达/AMD) AI大模型(OpenAI)
GLOBAL_SOURCES = [
    {"id": "samsung", "name": "三星", "en": "samsung", "url": "https://news.samsung.com/global/rss"},
    {"id": "skhynix", "name": "SK海力士", "en": "sk hynix", "url": "https://news.skhynix.com/en/feed/"},
    {"id": "openai", "name": "OpenAI", "en": "openai", "url": "https://openai.com/news/rss.xml", "max_items": 12},
    {"id": "nvidia", "name": "英伟达", "en": "nvidia", "url": "https://nvidianews.nvidia.com/rss.xml"},
    {"id": "amd", "name": "AMD", "en": "amd", "url": "https://ir.amd.com/news-events/press-releases/rss"},
    {"id": "cnbc", "name": "CNBC", "en": "cnbc", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "max_items": 15},
]

# ============ 全球(美股)科技核心标的行情 ============
# code: 新浪 gb_ 前缀; name: 显示名; sector: 所属赛道id (用于前端分组/着色)
US_STOCKS = [
    {"code": "nvda", "name": "英伟达", "sector": "gpu"},
    {"code": "amd", "name": "AMD", "sector": "gpu"},
    {"code": "tsm", "name": "台积电", "sector": "semi_chip"},
    {"code": "intc", "name": "英特尔", "sector": "semi_chip"},
    {"code": "asml", "name": "阿斯麦", "sector": "semi_eq"},
    {"code": "amat", "name": "应用材料", "sector": "semi_eq"},
    {"code": "lrcx", "name": "泛林集团", "sector": "semi_eq"},
    {"code": "mu", "name": "美光", "sector": "storage"},
    {"code": "avgo", "name": "博通", "sector": "optical"},
    {"code": "mrvl", "name": "迈威尔", "sector": "optical"},
    {"code": "anet", "name": "Arista", "sector": "optical"},
    {"code": "tsla", "name": "特斯拉", "sector": "robot"},
    {"code": "meta", "name": "Meta", "sector": "ai_model"},
    {"code": "goog", "name": "谷歌", "sector": "ai_model"},
    {"code": "msft", "name": "微软", "sector": "ai_model"},
    {"code": "orcl", "name": "甲骨文", "sector": "ai_app"},
    {"code": "pltr", "name": "Palantir", "sector": "ai_app"},
    {"code": "crm", "name": "赛富时", "sector": "ai_app"},
]

# 华尔街见闻中的 A 股/港股特征词 -> 归为国内资讯
A_SHARE_WORDS = ["沪指", "深成指", "创业板指", "科创50", "上证", "深证", "a股", "沪深",
                 "恒生", "港股", "北向", "南向", "科创板", "两市", "涨停", "跌停",
                 "沪市", "深市", "北交所", "主力资金", "尾盘", "盘中"]

GLOBAL_POOL = {}          # id -> item, 海外资讯去重池
GLOBAL_POOL_LOCK = __import__("threading").Lock()

WSCN_CHANNELS = ["us-stock-channel", "global-channel"]


def _build_item(item_id, full_time, title, content, link, source, region="global", drop_names=None):
    """构造统一资讯条目并分类标注。
    过滤规则: 未命中任何科技赛道 且 未命中人物表态 -> 返回 None (过滤无关新闻);
    命中人物表态但无赛道 -> 保留, 不设独立人物板块, 标签直接用人物名标注
    (这类新闻如"特朗普宣布关税"常不含科技关键词, 不能因赛道过滤而丢失)。
    drop_names: 分类时剔除的厂商名(中英文), 避免"三星所有新闻全归存储"式误分类"""
    text = title + " " + content
    cls_text = text
    for nm in drop_names or []:
        if nm:
            cls_text = re.sub(re.escape(nm), "", cls_text, flags=re.IGNORECASE)
    sid = classify_sector(cls_text)
    people = match_people(title, content)
    if sid is None and not people:
        return None
    sector = next((s for s in SECTORS if s["id"] == sid), None) if sid else None
    pnames = people_names(people)
    if region == "global" and any(w in text.lower() for w in A_SHARE_WORDS):
        region = "cn"           # 海外源中混入的 A 股/港股新闻归为国内
    imp = classify_importance(text)
    research = 1 if any(w in text.lower() for w in RESEARCH_WORDS) else 0
    if research and imp == "normal":
        imp = "important"       # 研报类资讯至少为"重要"
    return {
        "id": item_id, "region": region,
        "time": full_time[11:19] if len(full_time) >= 19 else full_time,
        "fullTime": full_time,
        "sector": sid,
        "sectorName": sector["name"] if sector else "·".join(pnames),
        "sectorColor": sector["color"] if sector else "#b37feb",
        "title": title, "content": content, "link": link,
        "source": source, "research": research,
        "people": people, "peopleNames": pnames,
        "impact": classify_impact(text), "importance": imp,
    }


def _first_sentence(text, max_len=200):
    """提取正文首句/首段作为摘要 (去HTML标签/空白/实体, 超长在句子边界截断)"""
    import html as _html
    t = re.sub(r"<[^>]+>", " ", text or "").strip()
    t = _html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    m = re.search(r"[.!?。！？](?:\s|$)", cut)
    if m:
        return cut[:m.end()].strip()
    sp = cut.rfind(" ")
    return (cut[:sp].strip() if sp > 30 else cut.strip()) + "…"


def fetch_wallstcn():
    """华尔街见闻 7x24 快讯 (美股/全球频道), 中文、实时、覆盖海外机构观点。
    每频道拉 2 页 (limit=100 + cursor 翻页) 覆盖约 5 天时间窗——人物表态是低频事件,
    仅 1 页(60h)时马斯克/奥特曼等掌门人表态几乎抓不到"""
    items = []
    for channel in WSCN_CHANNELS:
        base = ("https://api-one.wallstcn.com/apiv1/content/lives?"
                "channel={}&limit=100&client=pc".format(channel))
        cursor = ""
        for _page in range(2):
            url = base + ("&cursor=" + str(cursor) if cursor else "")
            try:
                data = json.loads(http_get(url, referer="https://wallstreetcn.com/"))
                d = data.get("data") or {}
                for it in (d.get("items") or []):
                    text = (it.get("content_text") or "").strip()
                    if not text:
                        continue
                    ts = it.get("display_time") or 0
                    full = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""
                    if not full:
                        continue
                    nid = "wscn:" + str(it.get("id") or "")
                    uri = it.get("uri") or ("https://wallstreetcn.com/livenews/" + str(it.get("id", "")))
                    title = _clean_title(text)
                    item = _build_item(nid, full, title, text, uri, "华尔街见闻")
                    if item:
                        items.append(item)
                cursor = d.get("next_cursor") or ""
                if not cursor:
                    break
            except Exception as e:
                print("[wallstcn] err:", channel, e)
                break
    return items


def fetch_vendor_rss():
    """厂商官方新闻 RSS (三星/SK海力士/OpenAI/英伟达/AMD)。每源取最新 max_items 条
    (默认40, OpenAI 限12以滤除客户案例博客), 分类时剔除厂商名, 避免通用公司新闻
    (电视/手机等)被误归入赛道。兼容 RSS2.0(<item>) 与 Atom(<entry>);
    英文标题自动翻译为中文 (titleZh)"""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    items = []
    for src in GLOBAL_SOURCES:
        try:
            xml_text = http_get(src["url"], referer="")
            root = ET.fromstring(xml_text)
            nodes = list(root.iter("item")) or list(root.iter("entry"))
            picked = 0
            max_items = src.get("max_items", 40)
            for it in nodes:
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                # 正文: RSS 用 description, Atom 用 content/summary
                desc = ""
                for tag in ("description", "content", "summary"):
                    el = it.find(tag)
                    if el is not None and el.text:
                        desc = el.text
                        break
                desc = re.sub(r"<[^>]+>", " ", desc).strip()
                # 链接: RSS 用 <link> 文本, Atom 用 <link href=...>
                link = (it.findtext("link") or "").strip()
                if not link:
                    for el in it.findall("link"):
                        href = el.get("href")
                        if href:
                            link = href
                            break
                # 时间: RSS 用 pubDate, Atom 用 published/updated
                pub = it.findtext("pubDate") or it.findtext("published") or it.findtext("updated") or ""
                full = ""
                try:
                    ts = parsedate_to_datetime(pub).timestamp()
                    full = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                except Exception:
                    continue
                if picked >= max_items:         # 每源最多 max_items 条 (feed 按时间倒序)
                    break
                nid = src["id"] + ":" + re.sub(r"\W", "", title)[:24]
                item = _build_item(nid, full, title, desc or title, link, src["name"],
                                   drop_names=[src["name"], src.get("en", "")])
                if item:
                    items.append(item)
                    picked += 1
                    # 摘要: 正文首句 (英文), 供翻译为中文要点 (digestZh)
                    sent = _first_sentence(desc)
                    if sent and sent != title:
                        item["digest"] = sent
        except Exception as e:
            print("[vendor-rss] err:", src["id"], e)
    # 英文标题+摘要翻译: 每源取最新 8 条, 线程池并行调 MyMemory (IO密集, 并发显著提速)
    from concurrent.futures import ThreadPoolExecutor
    per_src = {}
    for it in items:
        per_src.setdefault(it["source"], []).append(it)
    to_translate = []
    for src_items in per_src.values():
        src_items.sort(key=lambda x: x["fullTime"], reverse=True)
        to_translate.extend(src_items[:8])

    def _tr(it):
        zh_t = translate_en(it["title"])
        zh_d = translate_en(it.get("digest", "")) if it.get("digest") else ""
        return it, zh_t, zh_d

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_tr, to_translate))
    for it, zh_t, zh_d in results:
        if zh_t and zh_t != it["title"]:
            it["titleZh"] = zh_t
        if zh_d and it.get("digest") and zh_d != it["digest"]:
            it["digestZh"] = zh_d
    return items


def fetch_global_news():
    """聚合全部海外资讯 -> 全局池"""
    items = fetch_wallstcn() + fetch_vendor_rss()
    with GLOBAL_POOL_LOCK:
        for it in items:
            if it["id"] and it["id"] not in GLOBAL_POOL:
                GLOBAL_POOL[it["id"]] = it
        pool = sorted(GLOBAL_POOL.values(), key=lambda x: x["fullTime"], reverse=True)
        if len(pool) > 600:
            for old in pool[600:]:
                GLOBAL_POOL.pop(old["id"], None)
            pool = pool[:600]
    return pool


# 人物表态不再单独成板块/接口: PEOPLE/match_people 仅供 _build_item 打人名标签


# ============ 内容级去重 ============
# 同一事件常被多源转发/同一政策被拆条报道: id 不同但信息重复。检测两种形态:
#   (a) 同一政策文件拆条 / 同稿转发 -> 正文前缀高度重叠 (湖南机器人政策, 3条变1条)
#   (b) 异源转载, 前缀措辞不同但中后段重叠 -> 标题中高相似 + 正文中高重叠 (觅蜂融资)
# 规则: 按自然日分组, 组内满足任一判重通道 -> 视为同一条, 只保留信息最完整(正文更长/有链接)且更新的那条。
_DUP_SIM_TITLE = 0.68    # 通道2: 标题相似度阈值 (雅江/星辉模板标题 ratio≈0.72 但前缀不同, 不误杀)
_DUP_SIM_BODY = 0.75     # 通道4: 正文长窗相似度阈值 (同稿长文转发)
_DUP_COMB_TITLE = 0.55   # 通道5: 组合判定 - 标题相似度下限
_DUP_COMB_BODY = 0.50    # 通道5: 组合判定 - 正文相似度下限
_DUP_PREFIX = 6          # 通道2: 标题判重的前缀门槛
_DUP_NOISE = ("快讯", "突发", "最新", "独家", "刚刚", "早报", "晚报", "盘中")

# 通道6: 财报主体判重 —— 同一公司同一天的财报新闻, 各源标题角度不同
# (营收版/净利版/增速版, 标题相似度仅 0.3~0.5, 文本相似通道抓不住), 用
# "主体名(：前) + 财报关键词" 判重
_EARNINGS_WORDS = ("半年报", "半年度", "上半年", "三季报", "三季度", "三季度报",
                   "年报", "年度报告", "季报", "季度报告", "一季报", "财报",
                   "业绩", "净利润", "营收", "营业收入", "营业总收入")
# 非公司主体 (监管/政府机构名): 其财报词新闻可能是不同事件, 不做主体判重
_SUBJECT_BLACKLIST = ("证监会", "央行", "中国人民银行", "国务院", "财政部", "工信部",
                      "工业和信息化部", "发改委", "发展改革委", "商务部", "国资委",
                      "国家统计局", "统计局", "上交所", "深交所", "北交所", "港交所",
                      "交易所", "财政部们", "工信部等", "中注协", "银保监会", "金融监管总局")


def _earnings_subject(title):
    """财报类新闻的主体名。两种提取方式:
    1) '天孚通信：上半年净利润...' -> '：'前的公司名
    2) '天孚通信发布2026年半年度报告...' -> '发布/公布/披露'前的公司名
    非财报标题或主体可疑(监管机构/超长)返回 None"""
    t = (title or "").strip()
    if "：" in t:
        subj = t.split("：", 1)[0].strip()
    else:
        m = re.match(r"^(.{3,14}?)(?:发布|公布|披露)", t)
        if not m:
            return None
        subj = m.group(1).strip()
    if not (3 <= len(subj) <= 14):
        return None
    if subj in _SUBJECT_BLACKLIST:
        return None
    return subj if any(w in t for w in _EARNINGS_WORDS) else None


def _norm_title(t):
    """归一化标题: 小写、去噪音词、去标点空白, 保留中英文与数字"""
    t = (t or "").lower()
    for w in _DUP_NOISE:
        t = t.replace(w, "")
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]", "", t)


def _norm_body(t, n):
    """正文前 n 字符归一化, 用于同稿/同文件拆条检测"""
    return re.sub(r"[^\u4e00-\u9fa5a-z0-9]", "", (t or "").lower())[:n]


def _zh_ratio(t):
    """中文字符占比 (正文检测仅对中文正文生效, 避免英文模板新闻误判)"""
    if not t:
        return 0.0
    zh = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fa5")
    return zh / len(t)


def _ratio(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _dup_match(na, b30a, b100a, nb, b30b, b100b):
    """预归一化后的两条资讯判重 (六参数: 标题/正文30窗/正文100窗 x 两条)"""
    # 通道1: 标题归一化完全相同
    if na and nb and na == nb:
        return True
    # 通道2: 标题高度相似且开头一致 (防"XX成立新公司"式模板标题误杀不同公司)
    if (na and nb and len(na) >= _DUP_PREFIX and len(nb) >= _DUP_PREFIX
            and na[:_DUP_PREFIX] == nb[:_DUP_PREFIX]
            and abs(len(na) - len(nb)) <= 10
            and _ratio(na, nb) >= _DUP_SIM_TITLE):
        return True
    # 通道3: 正文前缀(30字窗)高度一致 -> 同一政策文件拆条 / 同稿转发
    if (b30a and b30b and len(b30a) >= 12 and len(b30b) >= 12
            and abs(len(b30a) - len(b30b)) <= 8 and _ratio(b30a, b30b) >= 0.9):
        return True
    # 通道4: 正文长窗(100字)高度相似 -> 同稿长文转发 (前缀不同也抓)
    if (b100a and b100b and len(b100a) >= 20 and len(b100b) >= 20
            and abs(len(b100a) - len(b100b)) <= 30
            and _ratio(b100a, b100b) >= _DUP_SIM_BODY):
        return True
    # 通道5: 组合判定 - 标题中高相似 且 正文中高重叠 -> 异源转载 (前缀措辞不同但中后段重叠)
    if (na and nb and abs(len(na) - len(nb)) <= 30
            and _ratio(na, nb) >= _DUP_COMB_TITLE
            and b100a and b100b and len(b100a) >= 20 and len(b100b) >= 20
            and abs(len(b100a) - len(b100b)) <= 30
            and _ratio(b100a, b100b) >= _DUP_COMB_BODY):
        return True
    return False


def dedupe_items(items):
    """内容级去重: 按自然日分组, 组内判重, 当天重复资讯只保留一条。
    仅影响输出列表, 不改 NEWS_POOL/GLOBAL_POOL 池本体 (池继续累积供增量判断)。"""
    by_day = {}
    for it in items:
        day = (it.get("fullTime") or "")[:10] or "unknown"
        by_day.setdefault(day, []).append(it)
    out = []
    for _day, lst in by_day.items():
        # 完整度优先: 正文更长 > 有链接 > 时间更新, 排序靠前者优先保留
        lst.sort(key=lambda x: (len(x.get("content") or ""),
                                bool(x.get("link")),
                                x.get("fullTime") or ""), reverse=True)
        kept, kept_meta, kept_subj = [], [], set()
        for it in lst:
            body = it.get("content") or ""
            na = _norm_title(it.get("title"))
            if _zh_ratio(body) >= 0.5:
                b30a, b100a = _norm_body(body, 30), _norm_body(body, 100)
            else:
                b30a = b100a = ""
            subj = _earnings_subject(it.get("title"))
            dup = False
            # 通道6: 同日同主体财报新闻 (各源标题角度不同, 文本通道抓不住)
            if subj and subj in kept_subj:
                dup = True
            if not dup:
                for nb, b30b, b100b in kept_meta:
                    if _dup_match(na, b30a, b100a, nb, b30b, b100b):
                        dup = True
                        break
            if not dup:
                kept.append(it)
                kept_meta.append((na, b30a, b100a))
                if subj:
                    kept_subj.add(subj)
        out.extend(kept)
    return sorted(out, key=lambda x: x["fullTime"], reverse=True)


def merge_news(cn, gl):
    """合并 A股 + 海外资讯, 内容级去重 (当天重复资讯只报一条), 按时间倒序"""
    return dedupe_items(list(cn) + list(gl))[:600]


# ============ 分时数据 ============
def fetch_trends():
    out = {}
    for secid, name, kid in INDEX_SECIDS:
        url = ("https://push2his.eastmoney.com/api/qt/stock/trends2/get?"
               "secid={}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
               "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ndays=1&iscr=0&_={}".format(
                   secid, int(time.time() * 1000)))
        try:
            data = json.loads(http_get(url, referer="https://quote.eastmoney.com/"))
            trends = (data.get("data") or {}).get("trends") or []
            pts = [float(t.split(",")[2]) for t in trends]  # f53 = 最新价
            out[kid] = pts[-80:] if pts else []
        except Exception as e:
            print("[trends] err:", secid, e)
            out[kid] = []
    return out


# ============ TTL 缓存 ============
class TTLCache:
    """TTL 缓存: 命中返回; 过期则触发后台线程刷新并返回旧值 (请求永不阻塞);
    无缓存时同步加载 (仅首次)"""

    def __init__(self):
        self.lock = __import__("threading").Lock()
        self.data = {}
        self.refreshing = set()

    def _load(self, key, ttl, loader):
        try:
            val = loader()
            with self.lock:
                self.data[key] = (time.time(), val)
        finally:
            with self.lock:
                self.refreshing.discard(key)

    def get(self, key, ttl, loader):
        now = time.time()
        with self.lock:
            c = self.data.get(key)
            if c and now - c[0] < ttl:
                return c[1]
            if key in self.refreshing:
                if c:
                    return c[1]
            elif c:
                self.refreshing.add(key)
                __import__("threading").Thread(
                    target=self._load, args=(key, ttl, loader), daemon=True).start()
                return c[1]
        return self._load(key, ttl, loader)   # 首次无缓存: 同步加载


cache = TTLCache()


# ============ HTTP Handler ============
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self.handle_api(path)
        else:
            super().do_GET()

    def handle_api(self, path):
        try:
            if path == "/api/overview":
                data = cache.get("overview", 2, load_overview)
                self.send_json({"ts": int(time.time() * 1000), **data})
            elif path == "/api/news":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                n = min(int(q.get("n", ["60"])[0]), 200)
                region = (q.get("region", ["all"])[0] or "all")
                cn = cache.get("news_cn", 10, lambda: fetch_news(200))
                gl = cache.get("news_global", 60, fetch_global_news)
                items = merge_news(cn, gl)
                if region in ("cn", "global"):
                    items = [it for it in items if it.get("region") == region]
                self.send_json({"ts": int(time.time() * 1000), "items": items[:n]})
            elif path == "/api/trends":
                trends = cache.get("trends", 60, fetch_trends)
                self.send_json({"ts": int(time.time() * 1000), "indices": trends})
            elif path == "/api/global":
                us = cache.get("us_market", 20, fetch_us_market)
                self.send_json({"ts": int(time.time() * 1000), "us": us})
            else:
                self.send_error(404)
        except Exception as e:
            print("[api] err:", path, e)
            self.send_json({"error": str(e)}, status=500)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


if __name__ == "__main__":
    import threading as _t

    def _prewarm_and_refresh():
        """启动预热 + 周期性后台刷新, 保证 API 请求始终命中缓存"""
        time.sleep(0.5)
        jobs = [("overview", 2, load_overview),
                ("news_cn", 10, lambda: fetch_news(200)),
                ("news_global", 60, fetch_global_news),
                ("us_market", 20, fetch_us_market),
                ("trends", 60, fetch_trends)]
        for k, ttl, fn in jobs:
            try:
                cache.get(k, ttl, fn)
            except Exception as e:
                print("[prewarm]", k, e)
        while True:
            time.sleep(20)
            try:
                cache.get("overview", 2, load_overview)
                cache.get("us_market", 20, fetch_us_market)
            except Exception as e:
                print("[bg]", e)

    _t.Thread(target=_prewarm_and_refresh, daemon=True).start()

    server = HTTPServer((HOST, PORT), Handler)
    print("科技内参行情代理已启动: http://{}:{}".format(HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print("\n已停止")
