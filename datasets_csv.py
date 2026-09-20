#!/usr/bin/env python3
"""
衛星データ解析に使える GT(正解・学習データ)・DEM・LULC のデータセット一覧を自動収集し、CSVにする。

  データ元(すべて無料・キー不要):
    TorchGeo             研究者が手で整備しているデータセット表(ライセンス・解像度つき。質が高い)
    Planetary Computer   Microsoftのデータカタログ(DEM・LULCの定番が揃う。範囲・期間つき)
    Zenodo               研究データの公開先(閲覧数・ダウンロード数つき)
    Hugging Face         機械学習データの公開先(ダウンロード数・いいね数つき)
  依存  : なし(Python標準ライブラリのみ)
  出力  : output/datasets.csv     最新の全件(毎回上書き。共有するのはこのファイル)
          output/new_日付.csv      前回から新しく見つかった分
  任意  : manual.csv を同じフォルダに置くと、その行も一覧に加える(自動収集で拾えない定番の追加用)

使い方:
  python datasets_csv.py
"""
import csv
import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ==================================================================
# 設定
# ==================================================================
ZENODO_QUERIES = {           # 種別のヒント: 検索語
    "DEM": ['"digital elevation model"', '"digital surface model" satellite', "DEM SRTM OR Copernicus OR TanDEM-X"],
    "LULC": ['"land cover" map satellite', '"land use" "land cover" classification', "LULC Sentinel-2 OR Landsat"],
    "GT": ['"remote sensing" benchmark dataset', '"training data" satellite imagery',
           '"ground truth" satellite', "Sentinel-1 labeled dataset", "Sentinel-2 labeled dataset",
           "SAR dataset deep learning", "InSAR dataset", "flood mapping dataset satellite",
           "building damage dataset satellite", "crop type dataset satellite",
           "deforestation dataset satellite", "ship detection dataset SAR",
           "change detection dataset satellite", "landslide dataset satellite"],
}
ZENODO_PAGES = 4             # 1検索語あたりのページ数(1ページ25件。キーなしの上限)
ZENODO_MIN_VIEWS = 0         # これ未満の閲覧数のものは載せない(ノイズが多ければ上げる)

HF_SEARCHES = ["remote sensing", "satellite", "sentinel", "landsat", "earth observation",
               "geospatial", "land cover", "elevation", "SAR", "torchgeo"]
HF_MIN_DOWNLOADS = 50        # 個人の試し置きを除くための下限

# 衛星・地球観測に関係するかの関門(Zenodo と Hugging Face にだけかける)
EO_GATE = ["remote sensing", "remotely sensed", "satellite", "earth observation", "spaceborne",
           "InSAR", "synthetic aperture radar", "SAR image", "SAR data", "Sentinel-1", "Sentinel-2",
           "Sentinel-3", "Landsat", "MODIS", "VIIRS", "ALOS", "PALSAR", "TerraSAR", "TanDEM-X",
           "RADARSAT", "ICEYE", "Capella", "Gaofen", "WorldView", "PlanetScope", "Pleiades",
           "Maxar", "GEDI", "ICESat", "SRTM", "Copernicus DEM", "ASTER", "hyperspectral",
           "multispectral", "aerial image", "orthophoto", "geospatial"]

DEM_WORDS = ["digital elevation", "digital surface model", "digital terrain model", "elevation model",
             "DEM", "DSM", "DTM", "SRTM", "bathymetr", "topograph"]
LULC_WORDS = ["land cover", "landcover", "land use", "land-use", "LULC", "cropland map",
              "crop map", "forest cover", "built-up", "impervious", "NLCD", "WorldCover", "CORINE",
              "Esri2020", "Cropland Data Layer", "NCCM", "Dynamic World", "GlobCover", "GLC_FCS"]
GT_WORDS = ["ground truth", "training data", "training set", "label", "annotat", "benchmark",
            "reference data", "in situ", "in-situ", "field survey", "validation data",
            "segmentation", "object detection", "change detection", "classification dataset",
            "building footprint", "damage", "deep learning", "machine learning", "biomass",
            "canopy height", "field boundar"]

USES = {                     # 用途の列に出すタグ
    "変状・InSAR": ["InSAR", "subsidence", "deformation", "displacement", "landslide"],
    "浸水": ["flood", "inundation", "water extent", "surface water"],
    "建物・被害": ["building", "damage", "earthquake", "footprint"],
    "火災": ["wildfire", "burned area", "burn scar", "fire"],
    "変化検知": ["change detection", "bi-temporal", "bitemporal"],
    "農業": ["crop", "field boundar", "agricultur", "yield", "paddy", "farmland"],
    "森林": ["forest", "deforestation", "biomass", "canopy", "tree", "mangrove"],
    "土地被覆": ["land cover", "land use", "LULC", "landcover"],
    "地形": ["elevation", "DEM", "terrain", "bathymetr"],
    "海洋・船舶": ["ship", "vessel", "oil spill", "marine", "sea ice"],
    "雲・前処理": ["cloud", "super-resolution", "super resolution"],
    "基盤モデル・事前学習": ["pretrain", "pre-train", "self-supervised", "foundation model", "embedding"],
}

# 名称だけでは用途が分からない有名データセットの手がかり
NAME_HINTS = {
    "建物・被害": ["xBD", "BRIGHT", "QuakeSet", "SpaceNet", "Inria", "Open Buildings", "GlobalBuildingMap"],
    "浸水": ["ETCI", "MMFlood", "Sen1Floods", "Flood-S1"],
    "火災": ["ChaBuD", "CaBuAr", "FireRisk", "Live Fuel"],
    "変化検知": ["LEVIR", "OSCD", "BRIGHT", "xBD"],
    "農業": ["PASTIS", "ZueriCrop", "EuroCrops", "AgriFieldNet", "Cashew", "Soybean", "NCCM",
             "Fields Of The World", "SustainBench"],
    "森林": ["TreeSatAI", "ReforesTree", "IDTReeS", "BioMassters", "Biomass-S3", "Meta CHM", "GlobBiomass"],
    "土地被覆": ["EuroSAT", "BigEarthNet", "SEN12MS", "LoveDA", "DFC20", "GID-15", "Chesapeake",
                 "SeasoNet", "So2Sat", "LCZ-S2", "LC100", "Esri2020", "NLCD", "EnviroAtlas", "GeoNRW"],
    "海洋・船舶": ["Marine Debris", "CaFFe"],
    "雲・前処理": ["L7 Irish", "L8 Biome", "Cloud-S", "WorldStrat"],
    "基盤モデル・事前学習": ["SSL4EO", "SatlasPretrain", "MMEarth", "Seasonal Contrast", "Million-AID",
                            "SkyScript", "S2-100k", "Copernicus-Pretrain", "HySpecNet"],
}

TASK_JA = {"C": "分類", "R": "回帰", "S": "セグメンテーション", "I": "インスタンス分割",
           "T": "時系列", "CD": "変化検知", "OD": "物体検出", "IC": "キャプション",
           "SR": "超解像", "LE": "位置エンコーディング"}

SAT_SOURCE = ["Sentinel", "Landsat", "Lansat", "MODIS", "VIIRS", "ALOS", "PALSAR", "TerraSAR",
              "TanDEM", "ENVISAT", "ERS-", "Capella", "Umbra", "Maxar", "MAXAR", "WorldView",
              "DigitalGlobe", "Gaofen", "Jilin", "Planet", "Pléiades", "Pleiades", "SPOT",
              "Himawari", "GOES", "EnMAP", "PRISMA", "Aster", "ASTER", "SRTM", "Google Earth",
              "Bing Imagery", "CNES", "Airbus", "SkySat", "Remote Sensing", "satellite", "DEM"]
AIR_SOURCE = ["Aerial", "aerial", "NAIP", "USGS National Map", "orthophoto", "Drone", "drone",
              "UAV", "LINZ", "Vaihingen", "HySpex", "LiDAR", "Lidar"]

# ==================================================================
ROOT = Path(__file__).parent
OUT = ROOT / "output"
UA = {"User-Agent": "eo-dataset-list/1.0 (monthly dataset catalogue)"}
CATEGORY_ORDER = {"DEM": 0, "LULC": 1, "GT・学習データ": 2}
ORIGIN_ORDER = {"手動追加": 0, "TorchGeo": 1, "Planetary Computer": 2, "Zenodo": 3, "Hugging Face": 4}

COLUMNS = [("category", "種別"), ("name", "名称"), ("uses", "用途"), ("platform", "観測手段"),
           ("sensor", "元データ・センサ"), ("resolution", "解像度(m)"), ("coverage", "対象範囲"),
           ("period", "対象期間"), ("task", "タスク・規模"), ("license", "ライセンス"),
           ("commercial", "商用利用(自動判定)"), ("downloads", "ダウンロード数"), ("views", "閲覧数"),
           ("likes", "いいね数"), ("provider", "提供元"), ("summary", "概要"), ("updated", "公開・更新日"),
           ("url", "入手先"), ("origin", "取得元")]


def log(msg):
    print(f"[datasets] {msg}", flush=True)


def pat(kw):
    e = re.escape(kw)
    if kw.isupper() and len(kw) <= 5:
        return re.compile(r"\b" + e + r"\b")
    return re.compile(r"(?<![A-Za-z])" + e, re.IGNORECASE)


_CACHE = {}


def hits(name, words, text):
    if name not in _CACHE:
        _CACHE[name] = [(w, pat(w)) for w in words]
    return [w for w, x in _CACHE[name] if x.search(text)]


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def short(s, n=140):
    s = strip_tags(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def http_get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = dict(UA)
    if "api.github.com" in url and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(30 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def get_json(url, params=None):
    return json.loads(http_get(url, params))


# ------------------------------------------------------------------
# 共通の判定
# ------------------------------------------------------------------
def commercial(lic):
    """ライセンス表記から商用利用の可否を機械的に推定する。最終確認は必ず原文で。"""
    s = str(lic or "").strip()
    if not s or s.lower() in ("unknown", "other", "-", "none"):
        return "不明"
    low = s.lower()
    nc = bool(re.search(r"(^|[^a-z])nc([^a-z]|$)|non-?commercial|noncommercial|research[- ]only", low))
    multi = bool(re.search(r"\b(and|or)\b|various|,", low))
    if nc:
        return "一部不可(要確認)" if multi else "不可"
    if multi:
        return "要確認(複数ライセンス)"
    if re.search(r"(^|[^a-z])nd([^a-z]|$)", low):
        return "可(改変不可)"
    if re.search(r"cc-?by-?sa|odbl|gpl|cdla-sharing|share", low):
        return "可(継承条件あり)"
    if re.search(r"cc0|cc-?by|public domain|pddl|odc-by|mit|apache|bsd|cdla-permissive|etalab|unlicense|ogl", low):
        return "可"
    return "要確認"


def category_of(title, text):
    """タイトル優先で DEM / LULC / GT を決める。どれでもなければ None。"""
    for cat, words in (("DEM", DEM_WORDS), ("LULC", LULC_WORDS)):
        if hits(cat, words, title):
            return cat
    if hits("GT", GT_WORDS, title + " " + text):
        return "GT・学習データ"
    for cat, words in (("DEM", DEM_WORDS), ("LULC", LULC_WORDS)):
        if len(hits(cat, words, text)) >= 2:
            return cat
    return None


def uses_of(text):
    return "、".join(u for u, words in USES.items()
                    if hits("use:" + u, words, text)
                    or any(h.lower() in text.lower() for h in NAME_HINTS.get(u, [])))


def platform_of(source):
    if any(w in source for w in SAT_SOURCE):
        return "衛星" + ("+航空" if any(w in source for w in AIR_SOURCE) else "")
    if any(w in source for w in AIR_SOURCE):
        return "航空・ドローン"
    return ""


def record(**kw):
    r = {k: "" for k, _ in COLUMNS}
    r.update(kw)
    r["commercial"] = commercial(r["license"])
    return r


# ------------------------------------------------------------------
# 1. TorchGeo(GitHub上のCSV)
# ------------------------------------------------------------------
TG_RAW = "https://raw.githubusercontent.com/microsoft/torchgeo/main/docs/api/datasets/"
TG_DOCS = "https://torchgeo.readthedocs.io/en/latest/api/datasets"


def clean_rst(s):
    s = re.sub(r":ref:`([^`<]+?)(?:\s*<[^>]*>)?`", r"\1", s or "")      # :ref:`Name`
    s = re.sub(r"`([^`<]+?)\s*<[^>]*>`_", r"\1", s)                     # `text <url>`_
    return s.replace("--", "〜").strip()


def fetch_torchgeo():
    try:    # データセットごとの解説ページ名を取得(失敗しても一覧ページにリンクするだけ)
        listing = get_json("https://api.github.com/repos/microsoft/torchgeo/contents/docs/api/datasets")
        slugs = {norm(x["name"][:-4]): x["name"][:-4] for x in listing if x["name"].endswith(".rst")}
    except Exception:
        slugs = {}
    out = []
    for fname in ("benchmark.csv", "pretraining.csv", "spacenet.csv", "copernicus_bench.csv", "masks.csv"):
        for row in csv.DictReader(io.StringIO(http_get(TG_RAW + fname))):
            name = clean_rst(row.get("Dataset", ""))
            source = clean_rst(row.get("Source", ""))
            platform = platform_of(source)
            if not name or not platform:                     # 衛星でも航空でもないもの(市民観測など)は除く
                continue
            if fname == "masks.csv":
                kind = row.get("Type", "")
                cat = "DEM" if kind == "DEM" else category_of(name, name) or "GT・学習データ"
                task = {"Masks": "ラスタの参照データ", "Geometries": "ベクタの参照データ",
                        "Points": "地点データ", "DEM": "標高"}.get(kind, kind)
            else:
                cat = "GT・学習データ"
                codes = [c.strip() for c in row.get("Task", "").split(",")]
                task = "・".join(TASK_JA.get(c, c) for c in codes if c and c != "-")
                n = clean_rst(row.get("# Samples", ""))
                if n and n != "-":
                    task += f"({n}サンプル)"
                if fname == "pretraining.csv":
                    task = "事前学習用 " + task
            slug = slugs.get(norm(name)) or slugs.get(norm(re.sub(r"\s*\d+$|-S\d.*$", "", name)))
            res = clean_rst(row.get("Resolution (m)", ""))
            out.append(record(
                category=cat, name=name, platform=platform, sensor=source,
                resolution="" if res == "-" else res, task=task,
                uses=uses_of(name + " " + row.get("Task", "").replace("CD", "change detection")),
                license=clean_rst(row.get("License", "")),
                provider="(解説ページ参照)", url=f"{TG_DOCS}/{slug}.html" if slug else TG_DOCS + ".html",
                origin="TorchGeo"))
    return out


# ------------------------------------------------------------------
# 2. Microsoft Planetary Computer(STACカタログ)
# ------------------------------------------------------------------
def coverage_of(bbox):
    try:
        w, s, e, n = [float(x) for x in bbox[:4]]
    except Exception:
        return ""
    if e - w > 300 and n - s > 100:
        return "全球"
    return f"経度{w:.0f}〜{e:.0f}、緯度{s:.0f}〜{n:.0f}"


def period_of(interval):
    try:
        a, b = interval[0]
        return f"{a[:4] if a else '?'}〜{b[:4] if b else '現在'}"
    except Exception:
        return ""


def fetch_planetary():
    data = get_json("https://planetarycomputer.microsoft.com/api/stac/v1/collections")
    out = []
    for c in data.get("collections", []):
        title = c.get("title") or c.get("id", "")
        text = " ".join([c.get("description", ""), " ".join(c.get("keywords") or [])])
        cat = category_of(title, text)
        if cat is None or (cat == "GT・学習データ" and not hits("DEM", DEM_WORDS, text)
                           and not hits("LULC", LULC_WORDS, text)
                           and not re.search(r"building|biomass|water|forest|canopy", text, re.I)):
            continue                                         # 画像そのもののコレクションは対象外
        ext = c.get("extent") or {}
        gsd = (c.get("summaries") or {}).get("gsd") or []
        out.append(record(
            category=cat, name=title, platform="衛星", uses=uses_of(title + " " + text),
            resolution="、".join(str(g) for g in gsd[:3]) if isinstance(gsd, list) else str(gsd),
            coverage=coverage_of(((ext.get("spatial") or {}).get("bbox") or [[]])[0]),
            period=period_of((ext.get("temporal") or {}).get("interval")),
            task="標高" if cat == "DEM" else "土地被覆図" if cat == "LULC" else "参照データ",
            license=c.get("license", ""),
            provider="、".join(p.get("name", "") for p in (c.get("providers") or [])[:2]),
            summary=short(c.get("msft:short_description") or c.get("description")),
            url=f"https://planetarycomputer.microsoft.com/dataset/{c.get('id', '')}",
            origin="Planetary Computer"))
    return out


# ------------------------------------------------------------------
# 3. Zenodo
# ------------------------------------------------------------------
def fetch_zenodo():
    out, seen = [], set()
    for hint, queries in ZENODO_QUERIES.items():
        for q in queries:
            for page in range(1, ZENODO_PAGES + 1):
                data = get_json("https://zenodo.org/api/records",
                                {"q": q, "type": "dataset", "size": 25, "page": page})
                found = (data.get("hits") or {}).get("hits") or []
                for h in found:
                    m = h.get("metadata") or {}
                    rid = str(h.get("conceptrecid") or h.get("id"))
                    rtype = ((m.get("resource_type") or {}).get("type") or "dataset")
                    if rid in seen or rtype != "dataset":
                        continue
                    seen.add(rid)
                    title = strip_tags(m.get("title"))
                    kws = m.get("keywords") or []
                    text = strip_tags(m.get("description")) + " " + " ".join(map(str, kws))
                    stats = h.get("stats") or {}
                    views = int(stats.get("unique_views") or stats.get("views") or 0)
                    if not hits("gate", EO_GATE, title + " " + text) or views < ZENODO_MIN_VIEWS:
                        continue
                    cat = category_of(title, text)
                    if not cat:
                        continue
                    lic = m.get("license") or {}
                    creators = m.get("creators") or []
                    gate = hits("gate", EO_GATE, title + " " + text)
                    out.append(record(
                        category=cat, name=title, uses=uses_of(title + " " + text),
                        platform=platform_of(" ".join(gate)), sensor="、".join(gate[:3]),
                        license=lic.get("id", "") if isinstance(lic, dict) else str(lic),
                        views=views,
                        downloads=int(stats.get("unique_downloads") or stats.get("downloads") or 0),
                        provider=(creators[0].get("affiliation") or creators[0].get("name", ""))
                        if creators else "",
                        summary=short(m.get("description")), updated=(m.get("publication_date") or "")[:10],
                        url="https://doi.org/" + h["doi"] if h.get("doi")
                        else f"https://zenodo.org/records/{h.get('id')}",
                        origin="Zenodo"))
                time.sleep(2)                                # 利用者登録なしの上限に配慮
                if len(found) < 25:
                    break
    return out


# ------------------------------------------------------------------
# 4. Hugging Face
# ------------------------------------------------------------------
def fetch_huggingface():
    out, seen = [], set()
    for term in HF_SEARCHES:
        params = {"search": term, "limit": 200, "full": "true", "sort": "downloads", "direction": -1}
        try:
            items = get_json("https://huggingface.co/api/datasets", params)
        except urllib.error.HTTPError:                       # 並び順の指定が通らない場合
            params.pop("sort"), params.pop("direction")
            items = get_json("https://huggingface.co/api/datasets", params)
        for d in items if isinstance(items, list) else []:
            did = d.get("id", "")
            dl = int(d.get("downloads") or 0)
            if not did or did in seen or dl < HF_MIN_DOWNLOADS:
                continue
            seen.add(did)
            tags = [str(t) for t in d.get("tags") or []]
            card = d.get("cardData") or {}
            text = " ".join([did.replace("/", " ").replace("-", " ").replace("_", " "),
                             " ".join(tags), str(card.get("pretty_name") or ""),
                             strip_tags(d.get("description"))])
            loose = re.sub(r"[-_]", " ", text)             # タグは remote-sensing のようにハイフン区切り
            gate = hits("gate", EO_GATE, text) or hits("gate", EO_GATE, loose)
            if not gate and not re.search(r"sentinel[\s_-]?[1-6]|eurosat|bigearthnet|torchgeo", text, re.I):
                continue
            title = did.split("/")[-1]
            cat = category_of(title.replace("-", " ").replace("_", " "), text) or "GT・学習データ"
            lic = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), "")
            tasks = [t.split(":", 1)[1] for t in tags if t.startswith("task_categories:")]
            out.append(record(
                category=cat, name=title, uses=uses_of(loose),
                platform=platform_of(" ".join(gate)), sensor="、".join(gate[:3]), task="、".join(tasks[:3]),
                license=lic, downloads=dl, likes=int(d.get("likes") or 0),
                provider=d.get("author") or did.split("/")[0],
                updated=str(d.get("lastModified") or "")[:10],
                url="https://huggingface.co/datasets/" + did, origin="Hugging Face"))
        time.sleep(1)
    return out


# ------------------------------------------------------------------
def load_manual():
    """manual.csv(任意)。列名は出力CSVと同じ日本語の見出しを使う。"""
    path = ROOT / "manual.csv"
    if not path.exists():
        return []
    back = {label: key for key, label in COLUMNS}
    rows = []
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        r = record(**{back[k]: v for k, v in row.items() if k in back and v})
        r["origin"] = "手動追加"
        if r["name"]:
            rows.append(r)
    return rows


def load_previous():
    path = OUT / "datasets.csv"
    if not path.exists():
        return []
    back = {label: key for key, label in COLUMNS}
    return [{back[k]: v for k, v in row.items() if k in back}
            for row in csv.DictReader(path.open(encoding="utf-8-sig"))]


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as fh:          # Excelで文字化けしない
        w = csv.writer(fh)
        w.writerow([label for _, label in COLUMNS])
        for r in rows:
            w.writerow([r.get(k, "") for k, _ in COLUMNS])


def main():
    OUT.mkdir(exist_ok=True)
    previous = load_previous()
    sources = [("TorchGeo", fetch_torchgeo), ("Planetary Computer", fetch_planetary),
               ("Zenodo", fetch_zenodo), ("Hugging Face", fetch_huggingface)]
    rows, failed = load_manual(), []
    for origin, fn in sources:
        try:
            got = fn()
            log(f"{origin}: {len(got)}件")
            rows += got
        except Exception as e:      # 落ちた取得元は前回の内容を引き継ぐ(公開中の一覧を痩せさせない)
            kept = [r for r in previous if r.get("origin") == origin]
            log(f"{origin}: 取得に失敗({e})。前回の{len(kept)}件を引き継ぎます")
            rows += kept
            failed.append(origin)
    if len(failed) == len(sources) and not previous:
        sys.exit("どの取得元にも接続できませんでした。CSVは作りません。")

    # 同じデータセットが複数の取得元にある場合は、情報の質が高い取得元を残す
    rows.sort(key=lambda r: ORIGIN_ORDER.get(r["origin"], 9))
    unique, names = [], set()
    for r in rows:
        key = norm(r["name"])
        if len(key) >= 5 and key in names:
            continue
        names.add(key)
        unique.append(r)

    def pop(r):
        return int(r.get("downloads") or 0) + int(r.get("views") or 0)
    unique.sort(key=lambda r: (CATEGORY_ORDER.get(r["category"], 9),
                               0 if str(r["platform"]).startswith("衛星") else 1,
                               ORIGIN_ORDER.get(r["origin"], 9) > 2, -pop(r), r["name"].lower()))

    seen_path = OUT / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    fresh = [r for r in unique if r["url"] not in seen]
    write_csv(OUT / "datasets.csv", unique)
    if seen and fresh:              # 初回は全部が「新着」になるので作らない
        write_csv(OUT / f"new_{dt.date.today().isoformat()}.csv", fresh)
    seen_path.write_text(json.dumps(sorted(seen | {r["url"] for r in unique})))

    for cat in CATEGORY_ORDER:
        log(f"{cat}: {sum(r['category'] == cat for r in unique)}件")
    log(f"完了: datasets.csv({len(unique)}件、うち新着{len(fresh) if seen else 0}件)")


if __name__ == "__main__":
    main()
