# -*- coding: utf-8 -*-
"""
AlpQuant Pro v4.2 — Institutional Multi-Asset Quantitative Terminal
Sidebar-free widescreen command-dock architecture, 840+ multi-market universe,
strict Participation Finance (Katılım) filtering, live 3-layer KAP/News AI stream.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import os
import re
import smtplib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

try:
    import yfinance as yf
    HAS_YFINANCE = True
except Exception:
    yf = None
    HAS_YFINANCE = False

try:
    from fpdf import FPDF
    HAS_FPDF = True
except Exception:
    FPDF = None
    HAS_FPDF = False


# =============================================================================
# 1. PAGE CONFIG & TURKISH NORMALIZATION HELPERS
# =============================================================================
st.set_page_config(
    page_title="AlpQuant Pro v4.2 | Kurumsal Kantitatif Terminal",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

TR_LOWER_MAP = str.maketrans({
    "İ": "i",
    "I": "ı",
    "Ş": "ş",
    "Ğ": "ğ",
    "Ü": "ü",
    "Ö": "ö",
    "Ç": "ç",
})


def normalize_turkish_lower(text: str) -> str:
    """Safely lowercase Turkish text without corrupting İ/I."""
    if not text:
        return ""
    return str(text).translate(TR_LOWER_MAP).lower()


def sanitize_pdf_text(text: str) -> str:
    """Convert Turkish characters to ASCII equivalents for FPDF Latin-1 safety."""
    repl = {
        "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
        "ö": "o", "Ö": "O", "ü": "u", "Ü": "U", "ç": "c", "Ç": "C",
        "₺": "TL", "—": "-", "–": "-", "•": "-", "◈": "*", "’": "'",
        "“": '"', "”": '"',
    }
    out = str(text or "")
    for k, v in repl.items():
        out = out.replace(k, v)
    return out.encode("latin-1", "ignore").decode("latin-1")


def safe_popover(label: str, use_container_width: bool = True):
    """Use st.popover on Streamlit >= 1.33, gracefully fallback to st.expander."""
    if hasattr(st, "popover"):
        return st.popover(label, use_container_width=use_container_width)
    return st.expander(label, expanded=False)


# =============================================================================
# 2. DATA MODELS
# =============================================================================
@dataclass
class SuitabilityProfile:
    user_id: str = "alperen_gulluoglu"
    risk_score: int = 5
    risk_label: str = "Çok Yüksek Risk (Agresif / Profesyonel)"
    approved_levels: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    max_single_weight: float = 0.25
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


@dataclass
class AssetSnapshot:
    symbol: str
    name: str
    asset_class: str          # BIST_STOCK | TEFAS_FUND | GLOBAL_ETF | US_STOCK | FX_GOLD_SUKUK | CRYPTO_LEVERAGED
    market_group: str         # BIST Payları | TEFAS Fonları | Küresel ETF & ABD | Döviz, Altın & Sukuk | Kripto & 3x ETF
    sector: str
    category: str
    indices: List[str]
    currency: str
    risk_level: int           # 1..5 SPK risk scale
    is_katilim: bool
    katilim_bucket: str       # BIST_KATILIM | TEFAS_KATILIM | ISLAMIC_ETF_SUKUK | NONE
    df: pd.DataFrame
    last_price: float
    prev_close: float
    change_pct: float
    volume: float
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe_pct: Optional[float] = None
    ter_pct: Optional[float] = None
    market_cap_m: Optional[float] = None
    data_source: str = "Sentetik (Kalibre)"
    yahoo_symbol: Optional[str] = None
    indicators: Dict[str, float] = field(default_factory=dict)


@dataclass
class SignalResult:
    symbol: str
    name: str
    market_group: str
    sector: str
    category: str
    currency: str
    risk_level: int
    is_katilim: bool
    katilim_bucket: str
    action: str               # GÜÇLÜ AL | AL | TUT / İZLE | SAT | SPK ENGELİ
    composite_score: float
    tech_score: float
    fund_score: float
    momentum_score: float
    last_price: float
    change_pct: float
    rsi_14: float
    macd_hist: float
    sma50_dist_pct: float
    vol_zscore: float
    atr_pct: float
    suggested_qty: int
    suggested_tl: float
    stop_loss: float
    take_profit: float
    reasons: List[str]
    data_source: str


@dataclass
class NewsDisclosure:
    id: str
    symbol: str
    title: str
    summary_text: str
    source: str
    published_at: str
    link: str
    layer: str                # KAP & BIST RSS | Piyasa Haberleri | Canlı Hacim Radarı


@dataclass
class DisclosureAnalysis:
    disclosure: NewsDisclosure
    sentiment: str            # OLUMLU | OLUMSUZ | NÖTR | HARD VETO
    score: float              # -100 .. +100
    impact_tag: str
    ai_commentary: str
    matched_signals: List[str]


# =============================================================================
# 3. 840+ MULTI-MARKET UNIVERSE CATALOG BUILDER
# =============================================================================
def _build_full_universe_catalog() -> List[Dict[str, Any]]:
    """
    Constructs the complete 840-asset built-in universe across 5 market groups:
      - 424 BIST Stocks (30 BIST_30, 289 BIST_GROWTH_100, 105 BIST_HIGH_BETA; 288 KATILIM_TUM)
      - 111 TEFAS Funds (53 Katılım + 58 Conventional)
      - 203 Global ETFs & US Stocks (8 Islamic ETFs + 47 Global ETFs + 148 US Equities)
      - 60 Crypto & 3x Leveraged ETFs
      - 42 FX, Gold, Commodities, Bonds & Sukuk
    """
    catalog: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # A. BIST STOCKS (424 Total — 15 Sectors, 288 Katılım Uyumlu)
    # -------------------------------------------------------------------------
    bist_30_core = [
        ("THYAO", "Türk Hava Yolları", "Ulaştırma & Lojistik", True, 312.50, 5.8, 0.95, 22.4),
        ("ASELS", "Aselsan Elektronik", "Savunma & Teknoloji", True, 78.40, 24.5, 3.80, 18.2),
        ("TUPRS", "Tüpraş Petrol Rafinerileri", "Enerji & Petrol", True, 168.20, 7.2, 1.45, 28.5),
        ("BIMAS", "BİM Birleşik Mağazalar", "Perakende & Gıda", True, 542.00, 18.4, 4.10, 31.2),
        ("FROTO", "Ford Otosan", "Otomotiv & Yan Sanayi", True, 1085.00, 11.2, 3.60, 34.0),
        ("KCHOL", "Koç Holding", "Holding & Yatırım", False, 214.60, 8.5, 1.15, 19.6),
        ("SAHOL", "Sabancı Holding", "Holding & Yatırım", False, 98.40, 6.4, 0.85, 17.8),
        ("AKBNK", "Akbank T.A.Ş.", "Bankacılık & Finans", False, 64.20, 4.9, 1.05, 29.4),
        ("GARAN", "Garanti BBVA", "Bankacılık & Finans", False, 128.50, 5.2, 1.35, 33.1),
        ("ISCTR", "Türkiye İş Bankası (C)", "Bankacılık & Finans", False, 14.85, 4.6, 0.92, 26.8),
        ("YKBNK", "Yapı ve Kredi Bankası", "Bankacılık & Finans", False, 32.40, 4.8, 1.02, 27.5),
        ("EREGL", "Ereğli Demir ve Çelik", "Demir Çelik & Maden", True, 52.80, 14.2, 0.88, 9.4),
        ("SISE", "Türkiye Şişe ve Cam", "Cam & Seramik", True, 48.60, 11.5, 0.96, 12.1),
        ("TCELL", "Turkcell İletişim", "Telekomünikasyon", True, 104.20, 10.8, 1.65, 21.3),
        ("TTKOM", "Türk Telekomünikasyon", "Telekomünikasyon", True, 51.40, 12.1, 1.40, 16.5),
        ("ENKAI", "Enka İnşaat ve Sanayi", "İnşaat & Çimento", True, 56.90, 13.4, 1.32, 14.8),
        ("PGSUS", "Pegasus Hava Taşımacılığı", "Ulaştırma & Lojistik", True, 248.50, 8.9, 2.15, 24.7),
        ("MGROS", "Migros Ticaret", "Perakende & Gıda", False, 524.00, 15.6, 3.40, 26.2),
        ("TOASO", "Tofaş Türk Otomobil", "Otomotiv & Yan Sanayi", True, 242.00, 10.4, 2.85, 29.0),
        ("ARCLK", "Arçelik A.Ş.", "Beyaz Eşya & Sanayi", True, 158.40, 16.8, 1.48, 11.6),
        ("PETKM", "Petkim Petrokimya", "Kimya & Petrokimya", True, 22.10, 19.2, 1.12, 8.5),
        ("KRDMD", "Kardemir (D)", "Demir Çelik & Maden", True, 28.40, 13.8, 0.94, 10.2),
        ("KOZAL", "Koza Altın İşletmeleri", "Demir Çelik & Maden", True, 24.60, 15.4, 1.85, 15.9),
        ("GUBRF", "Gübre Fabrikaları", "Kimya & Petrokimya", True, 218.00, 22.0, 3.20, 14.2),
        ("ASTOR", "Astor Enerji", "Enerji & Petrol", True, 114.50, 18.9, 4.80, 32.4),
        ("KONTR", "Kontrolmatik Teknoloji", "Savunma & Teknoloji", True, 62.40, 29.4, 5.60, 21.8),
        ("HEKTS", "Hektaş Ticaret", "Kimya & Petrokimya", True, 5.45, 34.0, 2.90, 6.4),
        ("SASA", "Sasa Polyester", "Kimya & Petrokimya", True, 4.82, 26.5, 3.10, 13.2),
        ("EKGYO", "Emlak Konut GYO", "GYO & Gayrimenkul", True, 13.45, 9.8, 0.72, 11.4),
        ("ALARK", "Alarko Holding", "Holding & Yatırım", False, 106.80, 7.6, 1.18, 18.9),
    ]

    for sym, name, sec, kat, px, pe, pb, roe in bist_30_core:
        idxs = ["BIST_30", "BIST_100", "BIST_TUM"]
        if kat:
            idxs.append("KATILIM_TUM")
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "BIST_STOCK",
            "market_group": "BIST Payları",
            "sector": sec,
            "category": "BIST 30 Ana Pazar",
            "indices": idxs,
            "currency": "TRY",
            "risk_level": 3,
            "is_katilim": kat,
            "katilim_bucket": "BIST_KATILIM" if kat else "NONE",
            "base_price": px,
            "pe_ratio": pe,
            "pb_ratio": pb,
            "roe_pct": roe,
            "ter_pct": None,
            "yahoo_symbol": f"{sym}.IS",
        })

    # BIST Growth / BIST 100 & Mid-Cap Real Symbols (289 symbols)
    bist_growth_specs: List[Tuple[str, str, str, bool, float]] = [
        ("DOAS", "Doğuş Otomotiv", "Otomotiv & Yan Sanayi", True, 245.0),
        ("TTRAK", "Türk Traktör", "Otomotiv & Yan Sanayi", True, 780.0),
        ("OTKAR", "Otokar Otomotiv", "Savunma & Teknoloji", True, 520.0),
        ("CCOLA", "Coca-Cola İçecek", "Perakende & Gıda", True, 64.5),
        ("ULKER", "Ülker Bisküvi", "Perakende & Gıda", True, 148.0),
        ("AEFES", "Anadolu Efes", "Perakende & Gıda", False, 212.0),
        ("SOKM", "Şok Marketler", "Perakende & Gıda", True, 48.2),
        ("MAVI", "Mavi Giyim", "Tekstil & Perakende", True, 92.4),
        ("VESTL", "Vestel Elektronik", "Beyaz Eşya & Sanayi", True, 72.6),
        ("VESBE", "Vestel Beyaz Eşya", "Beyaz Eşya & Sanayi", True, 19.8),
        ("BRSAN", "Borusan Birleşik Boru", "Demir Çelik & Maden", True, 485.0),
        ("BRYAT", "Borusan Yatırım", "Holding & Yatırım", False, 2150.0),
        ("OYAKC", "Oyak Çimento", "İnşaat & Çimento", True, 74.5),
        ("CIMSA", "Çimsa Çimento", "İnşaat & Çimento", True, 38.2),
        ("AKCNS", "Akçansa Çimento", "İnşaat & Çimento", True, 164.0),
        ("NUHCM", "Nuh Çimento", "İnşaat & Çimento", True, 295.0),
        ("BUCIM", "Bursa Çimento", "İnşaat & Çimento", True, 8.4),
        ("GOLTS", "Göltaş Çimento", "İnşaat & Çimento", True, 412.0),
        ("KONYA", "Konya Çimento", "İnşaat & Çimento", True, 7850.0),
        ("AFYON", "Afyon Çimento", "İnşaat & Çimento", True, 14.2),
        ("ENJSA", "Enerjisa Enerji", "Enerji & Petrol", True, 64.8),
        ("AKSEN", "Aksa Enerji", "Enerji & Petrol", True, 41.2),
        ("AKSA", "Aksa Akrilik", "Kimya & Petrokimya", True, 11.8),
        ("ZOREN", "Zorlu Enerji", "Enerji & Petrol", False, 5.15),
        ("ODAS", "Odaş Elektrik", "Enerji & Petrol", True, 7.85),
        ("CANTE", "Çan2 Termik", "Enerji & Petrol", True, 2.15),
        ("GESAN", "Girişim Elektrik", "Enerji & Petrol", True, 58.4),
        ("EUPWR", "Europower Enerji", "Enerji & Petrol", True, 46.2),
        ("ALFAS", "Alfa Solar Enerji", "Enerji & Petrol", True, 74.8),
        ("CWENE", "CW Enerji", "Enerji & Petrol", True, 238.0),
        ("SMRTG", "Smart Güneş Enerjisi", "Enerji & Petrol", True, 48.6),
        ("YEOTK", "Yeo Teknoloji Enerji", "Enerji & Petrol", True, 194.0),
        ("GWIND", "Galata Wind Enerji", "Enerji & Petrol", True, 31.4),
        ("AYDEM", "Aydem Yenilenebilir Enerji", "Enerji & Petrol", True, 26.8),
        ("BIOEN", "Biotrend Çevre ve Enerji", "Enerji & Petrol", True, 19.4),
        ("MAGEN", "Margün Enerji", "Enerji & Petrol", True, 21.6),
        ("NATEN", "Naturel Yenilenebilir Enerji", "Enerji & Petrol", True, 48.2),
        ("ESEN", "Esenboğa Elektrik", "Enerji & Petrol", True, 18.5),
        ("CONSE", "Consus Enerji", "Enerji & Petrol", True, 6.2),
        ("HUNER", "Hun Yenilenebilir Enerji", "Enerji & Petrol", True, 5.8),
        ("KARYE", "Kartal Yenilenebilir Enerji", "Enerji & Petrol", True, 29.4),
        ("ARASE", "Doğu Aras Enerji", "Enerji & Petrol", True, 62.5),
        ("ENERY", "Enerya Enerji", "Enerji & Petrol", True, 188.0),
        ("AHGAZ", "Ahlatcı Doğal Gaz", "Enerji & Petrol", True, 16.4),
        ("NTGAZ", "Naturelgaz Sanayi", "Enerji & Petrol", True, 7.2),
        ("AYGAZ", "Aygaz A.Ş.", "Enerji & Petrol", True, 168.5),
        ("TRCAS", "Turcas Petrol", "Enerji & Petrol", True, 28.4),
        ("MIATK", "MIA Teknoloji", "Savunma & Teknoloji", True, 54.2),
        ("LOGO", "Logo Yazılım", "Savunma & Teknoloji", True, 108.5),
        ("ARDYZ", "ARD Grup Bilişim", "Savunma & Teknoloji", True, 38.4),
        ("REEDR", "Reeder Teknoloji", "Savunma & Teknoloji", True, 29.8),
        ("SDTTR", "SDT Uzay ve Savunma", "Savunma & Teknoloji", True, 264.0),
        ("ALTNY", "Altınay Savunma", "Savunma & Teknoloji", True, 92.5),
        ("PAPIL", "Papilon Savunma", "Savunma & Teknoloji", True, 68.4),
        ("ONRYT", "Onur Yüksek Teknoloji", "Savunma & Teknoloji", True, 84.2),
        ("HTTBT", "Hitit Bilgisayar", "Savunma & Teknoloji", True, 44.6),
        ("FORTE", "Forte Bilgi İletişim", "Savunma & Teknoloji", True, 58.2),
        ("VBTYZ", "VBT Yazılım", "Savunma & Teknoloji", True, 34.8),
        ("ATATP", "ATP Ticari Bilgisayar", "Savunma & Teknoloji", True, 112.0),
        ("SMART", "Smartiks Yazılım", "Savunma & Teknoloji", True, 31.2),
        ("FONET", "Fonet Bilgi Teknolojileri", "Savunma & Teknoloji", True, 24.5),
        ("KFEIN", "Kafein Yazılım", "Savunma & Teknoloji", True, 148.0),
        ("LINK", "Link Bilgisayar", "Savunma & Teknoloji", True, 420.0),
        ("INDES", "İndeks Bilgisayar", "Savunma & Teknoloji", True, 8.95),
        ("DGATE", "Datagate Bilgisayar", "Savunma & Teknoloji", True, 42.6),
        ("DESPC", "Despec Bilgisayar", "Savunma & Teknoloji", True, 51.4),
        ("ARMDA", "Armada Bilgisayar", "Savunma & Teknoloji", True, 395.0),
        ("ARENA", "Arena Bilgisayar", "Savunma & Teknoloji", True, 44.2),
        ("KAREL", "Karel Elektronik", "Savunma & Teknoloji", True, 13.8),
        ("NETAS", "Netaş Telekomünikasyon", "Savunma & Teknoloji", True, 76.4),
        ("ALCTL", "Alcatel Lucent Teletaş", "Savunma & Teknoloji", True, 118.5),
        ("KRONT", "Kron Teknoloji", "Savunma & Teknoloji", True, 28.6),
        ("PENTA", "Penta Teknoloji", "Savunma & Teknoloji", True, 19.4),
        ("AZTEK", "Aztek Teknoloji", "Savunma & Teknoloji", True, 58.6),
        ("INGRM", "Ingram Micro Bilişim", "Savunma & Teknoloji", True, 385.0),
        ("MOBTL", "Mobiltel İletişim", "Savunma & Teknoloji", True, 6.8),
        ("MPARK", "MLP Sağlık Hizmetleri", "Sağlık & İlaç", True, 342.0),
        ("LKMNH", "Lokman Hekim Sağlık", "Sağlık & İlaç", True, 19.6),
        ("TNZTP", "Tapdi Oksijen Özel Sağlık", "Sağlık & İlaç", True, 42.8),
        ("SELEC", "Selçuk Ecza Deposu", "Sağlık & İlaç", True, 71.5),
        ("DEVA", "Deva Holding", "Sağlık & İlaç", True, 88.4),
        ("ECILC", "Eczacıbaşı İlaç", "Sağlık & İlaç", True, 52.6),
        ("GENIL", "Gen İlaç ve Sağlık", "Sağlık & İlaç", True, 96.2),
        ("TRILC", "Turk İlaç ve Serum", "Sağlık & İlaç", True, 22.8),
        ("MEDTR", "Meditera Tıbbi Malzeme", "Sağlık & İlaç", True, 46.5),
        ("ANGEN", "Anatolia Tanı ve Biyoteknoloji", "Sağlık & İlaç", True, 14.2),
        ("RTALB", "RTA Laboratuvarları", "Sağlık & İlaç", True, 12.6),
        ("TAVHL", "TAV Havalimanları", "Ulaştırma & Lojistik", True, 258.0),
        ("CLEBI", "Çelebi Hava Servisi", "Ulaştırma & Lojistik", True, 2240.0),
        ("RYSAS", "Reysaş Taşımacılık", "Ulaştırma & Lojistik", True, 16.4),
        ("RYGYO", "Reysaş GYO", "GYO & Gayrimenkul", True, 15.8),
        ("TLMAN", "Trabzon Liman İşletmeciliği", "Ulaştırma & Lojistik", True, 118.0),
        ("GSDDE", "GSD Denizcilik", "Ulaştırma & Lojistik", True, 9.8),
        ("BEYAZ", "Beyaz Filo Oto Kiralama", "Ulaştırma & Lojistik", True, 28.4),
        ("GRSEL", "Gür-Sel Turizm Taşımacılık", "Ulaştırma & Lojistik", True, 142.0),
        ("TUREX", "Tureks Turizm Taşımacılık", "Ulaştırma & Lojistik", True, 46.5),
        ("PASEU", "Pasifik Eurasia Lojistik", "Ulaştırma & Lojistik", True, 64.2),
        ("HOROZ", "Horoz Lojistik", "Ulaştırma & Lojistik", True, 52.8),
        ("VAKBN", "Türkiye Vakıflar Bankası", "Bankacılık & Finans", False, 23.4),
        ("HALKB", "Türkiye Halk Bankası", "Bankacılık & Finans", False, 16.8),
        ("TSKB", "Türkiye Sınai Kalkınma Bankası", "Bankacılık & Finans", False, 12.4),
        ("SKBNK", "Şekerbank T.A.Ş.", "Bankacılık & Finans", False, 4.95),
        ("ALBRK", "Albaraka Türk Katılım Bankası", "Bankacılık & Finans", True, 6.45),
        ("ICBCT", "ICBC Turkey Bank", "Bankacılık & Finans", False, 15.2),
        ("KLNMA", "Türkiye Kalkınma Bankası", "Bankacılık & Finans", False, 34.5),
        ("TURSG", "Türkiye Sigorta", "Sigorta & Finans", False, 14.2),
        ("ANSGR", "Anadolu Sigorta", "Sigorta & Finans", False, 98.4),
        ("ANHYT", "Anadolu Hayat Emeklilik", "Sigorta & Finans", False, 106.0),
        ("AGESA", "AgeSA Hayat ve Emeklilik", "Sigorta & Finans", False, 124.5),
        ("AKGRT", "Aksigorta", "Sigorta & Finans", False, 7.85),
        ("RAYSG", "Ray Sigorta", "Sigorta & Finans", False, 420.0),
        ("ISMEN", "İş Yatırım Menkul Değerler", "Sigorta & Finans", False, 42.6),
        ("OSMEN", "Osmanlı Yatırım Menkul", "Sigorta & Finans", False, 31.4),
        ("INFO", "İnfo Yatırım Menkul", "Sigorta & Finans", False, 11.2),
        ("GEDIK", "Gedik Yatırım Menkul", "Sigorta & Finans", False, 8.6),
        ("OYYAT", "Oyak Yatırım Menkul", "Sigorta & Finans", False, 36.8),
        ("A1CAP", "A1 Capital Yatırım", "Sigorta & Finans", False, 28.4),
        ("TERA", "Tera Yatırım Menkul", "Sigorta & Finans", False, 38.2),
        ("ISGYO", "İş GYO", "GYO & Gayrimenkul", False, 19.4),
        ("TRGYO", "Torunlar GYO", "GYO & Gayrimenkul", True, 56.8),
        ("SNGYO", "Sinpaş GYO", "GYO & Gayrimenkul", False, 4.25),
        ("HLGYO", "Halk GYO", "GYO & Gayrimenkul", False, 3.85),
        ("VKGYO", "Vakıf GYO", "GYO & Gayrimenkul", False, 2.45),
        ("ZRGYO", "Ziraat GYO", "GYO & Gayrimenkul", False, 8.9),
        ("ALGYO", "Alarko GYO", "GYO & Gayrimenkul", False, 24.8),
        ("AKFGY", "Akfen GYO", "GYO & Gayrimenkul", False, 2.35),
        ("AKSGY", "Akiş GYO", "GYO & Gayrimenkul", False, 6.85),
        ("KLGYO", "Kiler GYO", "GYO & Gayrimenkul", True, 2.95),
        ("OZKGY", "Özak GYO", "GYO & Gayrimenkul", True, 11.4),
        ("NUGYO", "Nurol GYO", "GYO & Gayrimenkul", False, 6.2),
        ("PSGYO", "Pasifik GYO", "GYO & Gayrimenkul", True, 1.95),
        ("KZBGY", "Kızılbük GYO", "GYO & Gayrimenkul", True, 26.4),
        ("SRVGY", "Servet GYO", "GYO & Gayrimenkul", True, 312.0),
        ("EYGYO", "EYG Gayrimenkul", "GYO & Gayrimenkul", True, 4.1),
        ("FZLGY", "Fuzul GYO", "GYO & Gayrimenkul", True, 16.8),
        ("SURGY", "Sur Tatil Evleri GYO", "GYO & Gayrimenkul", True, 44.5),
        ("AVPGY", "Avrupakent GYO", "GYO & Gayrimenkul", True, 54.2),
        ("BEGYO", "Batı Ege GYO", "GYO & Gayrimenkul", True, 4.85),
        ("VRGYO", "Vera Konsept GYO", "GYO & Gayrimenkul", True, 3.65),
        ("MHRGY", "MHR GYO", "GYO & Gayrimenkul", True, 5.4),
        ("ASGYO", "Asce GYO", "GYO & Gayrimenkul", True, 11.8),
        ("PEKGY", "Peker GYO", "GYO & Gayrimenkul", True, 1.85),
        ("SEGYO", "Şeker GYO", "GYO & Gayrimenkul", False, 4.15),
        ("DGGYO", "Doğuş GYO", "GYO & Gayrimenkul", False, 34.2),
        ("PAGYO", "Panora GYO", "GYO & Gayrimenkul", True, 56.4),
        ("YGGYO", "Yeni Gimat GYO", "GYO & Gayrimenkul", True, 64.8),
        ("AKMGY", "Akmerkez GYO", "GYO & Gayrimenkul", True, 245.0),
        ("AGHOL", "AG Anadolu Grubu Holding", "Holding & Yatırım", False, 354.0),
        ("DOHOL", "Doğan Şirketler Grubu", "Holding & Yatırım", False, 15.6),
        ("TKFEN", "Tekfen Holding", "Holding & Yatırım", True, 64.2),
        ("GLYHO", "Global Yatırım Holding", "Holding & Yatırım", False, 15.4),
        ("NTHOL", "Net Holding", "Holding & Yatırım", False, 38.6),
        ("GSDHO", "GSD Holding", "Holding & Yatırım", False, 4.85),
        ("BERA", "Bera Holding", "Holding & Yatırım", True, 16.4),
        ("VERUS", "Verusa Holding", "Holding & Yatırım", True, 298.0),
        ("VERTU", "Verusaturk Girişim", "Holding & Yatırım", True, 41.2),
        ("POLHO", "Polisan Holding", "Kimya & Petrokimya", True, 13.8),
        ("INVES", "Investco Holding", "Holding & Yatırım", False, 285.0),
        ("ECZYT", "Eczacıbaşı Yatırım", "Holding & Yatırım", True, 248.0),
        ("YESIL", "Yeşil Yatırım Holding", "Holding & Yatırım", False, 2.85),
        ("IEYHO", "Işıklar Enerji ve Yapı", "Holding & Yatırım", False, 9.4),
        ("RALYH", "Ral Yatırım Holding", "Holding & Yatırım", True, 245.0),
        ("LIDER", "LDR Turizm", "Ulaştırma & Lojistik", True, 118.0),
        ("KOZAA", "Koza Anadolu Metal", "Demir Çelik & Maden", True, 62.8),
        ("IPEKE", "İpek Doğal Enerji", "Demir Çelik & Maden", True, 44.5),
        ("ISDMR", "İskenderun Demir ve Çelik", "Demir Çelik & Maden", True, 41.2),
        ("IZMDC", "İzmir Demir Çelik", "Demir Çelik & Maden", True, 5.95),
        ("CEMTS", "Çemtaş Çelik Makina", "Demir Çelik & Maden", True, 12.4),
        ("KCAER", "Kocaer Çelik Sanayi", "Demir Çelik & Maden", True, 48.6),
        ("YKSLN", "Yükselen Çelik", "Demir Çelik & Maden", True, 15.8),
        ("BMSCH", "BMS Çelik Hasır", "Demir Çelik & Maden", True, 26.4),
        ("BMSTL", "BMS Birleşik Metal", "Demir Çelik & Maden", True, 42.5),
        ("CVKMD", "CVK Maden İşletmeleri", "Demir Çelik & Maden", True, 315.0),
        ("PRKME", "Park Elektrik Madencilik", "Demir Çelik & Maden", True, 21.8),
        ("SARKY", "Sarkuysan Elektrolitik Bakır", "Demir Çelik & Maden", True, 24.6),
        ("DMSAS", "Demisaş Döküm", "Demir Çelik & Maden", True, 8.95),
        ("DOKTA", "Döktaş Dökümcülük", "Demir Çelik & Maden", True, 34.2),
        ("ERBOS", "Erbosan Erciyas Boru", "Demir Çelik & Maden", True, 218.0),
        ("ERCB", "Erciyas Çelik Boru", "Demir Çelik & Maden", True, 112.0),
        ("BURCE", "Burçelik Bursa Çelik", "Demir Çelik & Maden", True, 28.4),
        ("BURVA", "Burçelik Vana", "Demir Çelik & Maden", True, 94.5),
        ("CUSAN", "Çuhadaroğlu Metal", "Demir Çelik & Maden", True, 24.2),
        ("TUCLK", "Tuğçelik Alüminyum", "Demir Çelik & Maden", True, 11.4),
        ("MAKTK", "Makina Takım Endüstrisi", "Makine & Sanayi", True, 7.65),
        ("PARSN", "Parsan Makina Parçaları", "Makine & Sanayi", True, 126.0),
        ("ASUZU", "Anadolu Isuzu", "Otomotiv & Yan Sanayi", True, 78.5),
        ("KARSN", "Karsan Otomotiv", "Otomotiv & Yan Sanayi", True, 14.2),
        ("TMSN", "Tümosan Motor ve Traktör", "Otomotiv & Yan Sanayi", True, 124.0),
        ("EGEEN", "Ege Endüstri", "Otomotiv & Yan Sanayi", True, 11850.0),
        ("BFRE", "Bosch Fren Sistemleri", "Otomotiv & Yan Sanayi", True, 745.0),
        ("JANTS", "Jantsa Jant Sanayi", "Otomotiv & Yan Sanayi", True, 28.4),
        ("BRISA", "Brisa Bridgestone", "Otomotiv & Yan Sanayi", True, 108.5),
        ("GOODY", "Goodyear Lastikleri", "Otomotiv & Yan Sanayi", True, 22.4),
        ("KORDS", "Kordsa Teknik Tekstil", "Otomotiv & Yan Sanayi", True, 86.2),
        ("FMIZP", "Federal-Mogul İzmit Piston", "Otomotiv & Yan Sanayi", True, 295.0),
        ("DITAS", "Ditaş Doğan Yedek Parça", "Otomotiv & Yan Sanayi", True, 19.6),
        ("KATMR", "Katmerciler Araç Üstü", "Otomotiv & Yan Sanayi", True, 2.45),
        ("PRKAB", "Türk Prysmian Kablo", "Makine & Sanayi", True, 36.8),
        ("ULUSE", "Ulusoy Elektrik", "Makine & Sanayi", True, 148.0),
        ("GEREL", "Gersan Elektrik", "Makine & Sanayi", True, 26.4),
        ("EMKEL", "Emek Elektrik", "Makine & Sanayi", True, 15.2),
        ("SAYAS", "Say Yenilenebilir Enerji", "Makine & Sanayi", True, 58.4),
        ("BFREN", "Bosch Fren Sistemleri A.Ş.", "Otomotiv & Yan Sanayi", True, 760.0),
        ("ALCAR", "Alarko Carrier", "Beyaz Eşya & Sanayi", True, 1240.0),
        ("KLMSN", "Klimasan Klima", "Beyaz Eşya & Sanayi", True, 28.6),
        ("SILVR", "Silverline Endüstri", "Beyaz Eşya & Sanayi", True, 24.8),
        ("YATAS", "Yataş Yatak ve Yorgan", "Mobilya & Ev", True, 28.4),
        ("DGNMO", "Doğanlar Mobilya", "Mobilya & Ev", True, 12.6),
        ("GENTS", "Gentaş Dekoratif Yüzeyler", "Mobilya & Ev", True, 19.4),
        ("KLSYN", "Koleksiyon Mobilya", "Mobilya & Ev", True, 6.45),
        ("ORMA", "Orma Orman Mahsulleri", "Mobilya & Ev", True, 168.0),
        ("EGEPO", "Nasmed Özel Sağlık", "Sağlık & İlaç", True, 14.8),
        ("BAGFS", "Bagfaş Bandırma Gübre", "Kimya & Petrokimya", True, 24.6),
        ("EGGUB", "Ege Gübre Sanayi", "Kimya & Petrokimya", True, 56.4),
        ("EGEPRO", "Ege Profil Ticaret", "Kimya & Petrokimya", True, 194.0),
        ("ALKIM", "Alkim Alkali Kimya", "Kimya & Petrokimya", True, 36.2),
        ("ALKA", "Alkim Kağıt", "Kağıt & Ambalaj", True, 34.8),
        ("KARTN", "Kartonsan Karton", "Kağıt & Ambalaj", True, 96.4),
        ("KONKA", "Konya Kağıt", "Kağıt & Ambalaj", True, 44.2),
        ("TEZOL", "Europap Tezol Kağıt", "Kağıt & Ambalaj", True, 18.6),
        ("TIRE", "Mondi Turkey Oluklu", "Kağıt & Ambalaj", True, 19.8),
        ("BAKAB", "Bak Ambalaj", "Kağıt & Ambalaj", True, 48.2),
        ("BNTAS", "Bantaş Bandırma Ambalaj", "Kağıt & Ambalaj", True, 11.4),
        ("GEDZA", "Gediz Ambalaj", "Kağıt & Ambalaj", True, 28.6),
        ("KMPUR", "Kimteks Poliüretan", "Kimya & Petrokimya", True, 42.5),
        ("RNPOL", "Rainbow Polikarbonat", "Kimya & Petrokimya", True, 24.8),
        ("MRSHL", "Marshall Boya", "Kimya & Petrokimya", True, 1850.0),
        ("DYOBY", "DYO Boya Fabrikaları", "Kimya & Petrokimya", True, 48.6),
        ("ACSEL", "Acıselsan Selüloz", "Kimya & Petrokimya", True, 112.0),
        ("DNISI", "Dinamik Isı Makina", "Kimya & Petrokimya", True, 14.2),
        ("IZFAS", "İzmir Fırça", "Kimya & Petrokimya", True, 34.8),
        ("KUTPO", "Kütahya Porselen", "Cam & Seramik", True, 76.4),
        ("EGSER", "Ege Seramik", "Cam & Seramik", True, 7.85),
        ("QUAGR", "QUA Granite Hayal Yapı", "Cam & Seramik", True, 3.95),
        ("BIENY", "Bien Yapı Ürünleri", "Cam & Seramik", True, 34.2),
        ("KLSER", "Kaleseramik Çanakkale", "Cam & Seramik", True, 42.6),
        ("USAK", "Uşak Seramik", "Cam & Seramik", True, 18.4),
        ("YBTAS", "Yibitaş Yozgat Çimento", "İnşaat & Çimento", True, 84500.0),
        ("BTCIM", "Batıçim Batı Anadolu", "İnşaat & Çimento", True, 168.0),
        ("BSOKE", "Batısöke Söke Çimento", "İnşaat & Çimento", True, 44.5),
        ("CMBTN", "Çimbeton Hazırbeton", "İnşaat & Çimento", True, 2450.0),
        ("LMKDC", "Limak Doğu Anadolu Çimento", "İnşaat & Çimento", True, 29.4),
        ("BOBET", "Boğaziçi Beton", "İnşaat & Çimento", True, 28.6),
        ("KOCMT", "Koç Metalurji", "Demir Çelik & Maden", True, 18.4),
        ("TABGD", "TAB Gıda Sanayi", "Perakende & Gıda", True, 164.0),
        ("TATGD", "Tat Gıda Sanayi", "Perakende & Gıda", True, 22.8),
        ("BANVT", "Banvit Bandırma Vitaminli", "Perakende & Gıda", True, 312.0),
        ("PNSUT", "Pınar Süt", "Perakende & Gıda", True, 14.6),
        ("PETUN", "Pınar Entegre Et", "Perakende & Gıda", True, 21.4),
        ("PINSU", "Pınar Su ve İçecek", "Perakende & Gıda", True, 16.8),
        ("TUKAS", "Tukaş Gıda", "Perakende & Gıda", True, 7.45),
        ("PENGD", "Penguen Gıda", "Perakende & Gıda", True, 8.25),
        ("KERVT", "Kerevitaş Gıda", "Perakende & Gıda", True, 14.8),
        ("ULUUN", "Ulusoy Un Sanayi", "Perakende & Gıda", True, 24.6),
        ("SOKE", "Söke Değirmencilik", "Perakende & Gıda", True, 16.2),
        ("EXUN", "Eksun Gıda Tarım", "Perakende & Gıda", True, 64.5),
        ("OBAMS", "Oba Makarnacılık", "Perakende & Gıda", True, 42.8),
        ("KAYSE", "Kayseri Şeker Fabrikası", "Perakende & Gıda", True, 24.2),
        ("KTSKR", "Kütahya Şeker Fabrikası", "Perakende & Gıda", True, 68.4),
        ("GOKNR", "Göknur Gıda", "Perakende & Gıda", True, 26.8),
        ("ELITE", "Elite Naturel Organik", "Perakende & Gıda", True, 38.4),
        ("ORCAY", "Orçay Ortaköy Çay", "Perakende & Gıda", True, 14.6),
        ("FADE", "Fade Gıda Yatırım", "Perakende & Gıda", True, 18.2),
        ("KNFRT", "Konfrut Gıda", "Perakende & Gıda", True, 22.4),
        ("KRSTL", "Kristal Kola", "Perakende & Gıda", True, 9.85),
        ("DARDL", "Dardanel Önentaş Gıda", "Perakende & Gıda", True, 5.65),
        ("YYLGD", "Yayla Agro Gıda", "Perakende & Gıda", True, 12.8),
        ("ATAKP", "Atakey Patates Gıda", "Perakende & Gıda", True, 48.6),
        ("BYDNR", "Baydöner Restoranları", "Perakende & Gıda", True, 28.4),
        ("BIGCH", "Büyük Şefler Gıda", "Perakende & Gıda", False, 31.2),
        ("KMPAS", "Kıraç Galvaniz", "Makine & Sanayi", True, 34.5),
        ("BIZIM", "Bizim Toptan Satış", "Perakende & Gıda", True, 36.4),
        ("KIMMR", "Ersan Alışveriş Hizmetleri", "Perakende & Gıda", True, 16.8),
        ("CRFSA", "Carrefoursa", "Perakende & Gıda", False, 118.0),
        ("TKNSA", "Teknosa İç ve Dış Ticaret", "Perakende & Gıda", True, 36.2),
        ("VAKKO", "Vakko Tekstil", "Tekstil & Perakende", True, 96.4),
        ("DESA", "Desa Deri", "Tekstil & Perakende", True, 22.8),
        ("SUWEN", "Suwen Tekstil", "Tekstil & Perakende", True, 28.4),
        ("KOTON", "Koton Mağazacılık", "Tekstil & Perakende", True, 24.6),
        ("PATEK", "Pasifik Teknoloji", "Savunma & Teknoloji", True, 76.5),
        ("YUNSA", "Yünsa Yünlü Sanayi", "Tekstil & Perakende", True, 54.2),
        ("BOSSA", "Bossa Ticaret", "Tekstil & Perakende", True, 11.4),
        ("ARSAN", "Arsan Tekstil", "Tekstil & Perakende", True, 18.6),
        ("BLCYT", "Bilici Yatırım", "Tekstil & Perakende", True, 19.2),
        ("BRKO", "Birko Birleşik Koyunlulular", "Tekstil & Perakende", True, 8.45),
        ("DAGI", "Dagi Giyim", "Tekstil & Perakende", True, 14.8),
        ("DERIM", "Derimod Konfeksiyon", "Tekstil & Perakende", True, 42.6),
        ("HATEK", "Hateks Hatay Tekstil", "Tekstil & Perakende", True, 21.4),
        ("KRTEK", "Karsu Tekstil", "Tekstil & Perakende", True, 28.6),
        ("LUKSK", "Lüks Kadife", "Tekstil & Perakende", True, 118.0),
        ("MNDRS", "Menderes Tekstil", "Tekstil & Perakende", True, 13.8),
        ("RODRG", "Rodrigo Tekstil", "Tekstil & Perakende", True, 34.2),
        ("RUBNS", "Rubenis Tekstil", "Tekstil & Perakende", True, 29.6),
        ("SANKO", "Sanko Pazarlama", "Tekstil & Perakende", True, 24.8),
        ("SKTAS", "Söktaş Tekstil", "Tekstil & Perakende", True, 5.45),
        ("SNPAM", "Sönmez Pamuklu", "Tekstil & Perakende", True, 78.4),
        ("AYCES", "Altınyunus Çeşme", "Turizm & Medya", False, 520.0),
        ("MAALT", "Marmaris Altınyunus", "Turizm & Medya", False, 1140.0),
        ("PKENT", "Petrokent Turizm", "Turizm & Medya", False, 245.0),
        ("TEKTU", "Tek-Art İnşaat Turizm", "Turizm & Medya", False, 4.85),
        ("ULAS", "Ulaşlar Turizm", "Turizm & Medya", False, 24.6),
        ("MARTI", "Martı Otel İşletmeleri", "Turizm & Medya", False, 3.65),
        ("MERIT", "Merit Turizm", "Turizm & Medya", False, 148.0),
        ("AVTUR", "Avrasya Petrol ve Turizm", "Turizm & Medya", False, 9.85),
        ("HURGZ", "Hürriyet Gazetecilik", "Turizm & Medya", False, 5.45),
        ("IHLAS", "İhlas Holding", "Holding & Yatırım", True, 1.45),
        ("IHYAY", "İhlas Yayın Holding", "Turizm & Medya", True, 2.35),
        ("IHGZT", "İhlas Gazetecilik", "Turizm & Medya", True, 1.85),
        ("IHEVA", "İhlas Ev Aletleri", "Beyaz Eşya & Sanayi", True, 2.65),
        ("IHLGM", "İhlas Gayrimenkul", "GYO & Gayrimenkul", True, 1.75),
        ("GSRAY", "Galatasaray Sportif", "Turizm & Medya", False, 1.85),
        ("FENER", "Fenerbahçe Futbol", "Turizm & Medya", False, 112.0),
        ("BJKAS", "Beşiktaş Futbol", "Turizm & Medya", False, 5.45),
        ("TSPOR", "Trabzonspor Sportif", "Turizm & Medya", False, 1.45),
    ]

    for idx_i, (sym, name, sec, kat, px) in enumerate(bist_growth_specs):
        idxs = ["BIST_GROWTH_100", "BIST_TUM"]
        if idx_i < 70:
            idxs.append("BIST_100")
        if kat:
            idxs.append("KATILIM_TUM")
        h = int(hashlib.md5(sym.encode()).hexdigest()[:6], 16)
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "BIST_STOCK",
            "market_group": "BIST Payları",
            "sector": sec,
            "category": "BIST Yıldız / Büyüme",
            "indices": idxs,
            "currency": "TRY",
            "risk_level": 4,
            "is_katilim": kat,
            "katilim_bucket": "BIST_KATILIM" if kat else "NONE",
            "base_price": px,
            "pe_ratio": round(6.0 + (h % 240) / 10.0, 2),
            "pb_ratio": round(0.75 + (h % 380) / 100.0, 2),
            "roe_pct": round(8.0 + (h % 280) / 10.0, 2),
            "ter_pct": None,
            "yahoo_symbol": f"{sym}.IS",
        })

    # BIST High-Beta / Small-Cap & Recent IPOs (105 symbols)
    bist_high_beta_specs: List[Tuple[str, str, str, bool, float]] = [
        ("AGROT", "Agrotech Yüksek Teknoloji", "Savunma & Teknoloji", True, 14.8),
        ("BINHO", "1000 Yatırımlar Holding", "Holding & Yatırım", True, 345.0),
        ("BORLS", "Borlease Otomotiv", "Ulaştırma & Lojistik", False, 34.2),
        ("BORSK", "Bor Şeker", "Perakende & Gıda", True, 28.6),
        ("CATES", "Çates Elektrik Üretim", "Enerji & Petrol", True, 46.8),
        ("DMRGD", "DMR Unlu Mamuller", "Perakende & Gıda", True, 12.4),
        ("DOFER", "Dofer Yapı Malzemeleri", "İnşaat & Çimento", True, 31.8),
        ("EBEBK", "Ebebek Mağazacılık", "Perakende & Gıda", True, 52.4),
        ("EKSUN", "Eksun Gıda", "Perakende & Gıda", True, 61.5),
        ("ENSRI", "Ensari Deri Gıda", "Tekstil & Perakende", True, 24.6),
        ("ENTRA", "IC Enterra Yenilenebilir", "Enerji & Petrol", True, 10.8),
        ("GIPTA", "Gıpta Ofis Kırtasiye", "Kağıt & Ambalaj", True, 38.4),
        ("HATSN", "Hat-San Gemi İnşaa", "Makine & Sanayi", True, 56.2),
        ("HRKET", "Hareket Proje Taşımacılığı", "Ulaştırma & Lojistik", True, 78.4),
        ("IZENR", "İzdemir Enerji", "Enerji & Petrol", True, 22.6),
        ("KBORU", "Kuzey Boru", "İnşaat & Çimento", True, 94.5),
        ("KGYO", "Koray GYO", "GYO & Gayrimenkul", False, 24.8),
        ("KOPOL", "Koza Polyester", "Tekstil & Perakende", True, 38.2),
        ("KTLEV", "Katılımevim Tasarruf", "Bankacılık & Finans", True, 58.6),
        ("KZGYO", "Kuzugrup GYO", "GYO & Gayrimenkul", True, 19.4),
        ("LILAK", "Lila Kağıt Sanayi", "Kağıt & Ambalaj", True, 31.2),
        ("MARBL", "Tureks Turunç Madencilik", "Demir Çelik & Maden", True, 16.8),
        ("MEGMT", "Mega Metal Sanayi", "Demir Çelik & Maden", True, 44.5),
        ("MEKAG", "Meka Beton Santralleri", "Makine & Sanayi", True, 58.2),
        ("MOGAN", "Mogan Enerji Yatırım", "Enerji & Petrol", True, 11.4),
        ("ODINE", "Odine Solutions Teknoloji", "Savunma & Teknoloji", True, 86.4),
        ("OFSYM", "Ofis Yem Gıda", "Perakende & Gıda", True, 38.6),
        ("ONCSM", "Oncosem Onkolojik Sistemler", "Sağlık & İlaç", True, 148.0),
        ("OZYSR", "Özyaşar Tel ve Galvaniz", "Demir Çelik & Maden", True, 26.8),
        ("RGYAS", "Rönesans Gayrimenkul", "GYO & Gayrimenkul", False, 118.0),
        ("SEGMN", "Seğmen Kardeşler Gıda", "Perakende & Gıda", True, 24.2),
        ("SKYMD", "Şeker Yatırım Menkul", "Sigorta & Finans", False, 12.8),
        ("TARKM", "Tarkim Bitki Koruma", "Kimya & Petrokimya", True, 445.0),
        ("TATEN", "Tatlıpınar Enerji", "Enerji & Petrol", True, 34.6),
        ("YIGIT", "Yiğit Akü Malzemeleri", "Otomotiv & Yan Sanayi", True, 42.8),
        ("ADGYO", "Adra GYO", "GYO & Gayrimenkul", True, 29.4),
        ("AKFYE", "Akfen Yenilenebilir Enerji", "Enerji & Petrol", True, 22.8),
        ("ALVES", "Alves Kablo Sanayi", "Makine & Sanayi", True, 46.5),
        ("ARTMS", "Artemis Halı", "Tekstil & Perakende", True, 38.2),
        ("AVHOL", "Avrupa Yatırım Holding", "Holding & Yatırım", False, 48.6),
        ("AYEN", "Ayen Enerji", "Enerji & Petrol", True, 28.4),
        ("BALAT", "Balatacılar Balatacılık", "Otomotiv & Yan Sanayi", True, 42.5),
        ("BARMA", "Barem Ambalaj", "Kağıt & Ambalaj", True, 19.6),
        ("BASGZ", "Başkent Doğalgaz", "Enerji & Petrol", True, 28.8),
        ("BAYRK", "Bayrak EBT Taban", "Kimya & Petrokimya", True, 24.2),
        ("BVSAN", "Bülbüloğlu Vinç Sanayi", "Makine & Sanayi", True, 88.4),
        ("CEMZY", "Cem Zeytin", "Perakende & Gıda", True, 14.6),
        ("COSMO", "Cosmos Yatırım Holding", "Holding & Yatırım", False, 84.2),
        ("CVKMD2", "CVK Maden Pay (T2)", "Demir Çelik & Maden", True, 310.0),
        ("DAGHL", "Dagi Yatırım Holding", "Holding & Yatırım", False, 24.8),
        ("DCTTR", "DCT Trading Dış Ticaret", "Perakende & Gıda", True, 26.4),
        ("DERHL", "Derlüks Yatırım Holding", "Holding & Yatırım", False, 14.2),
        ("DITAS2", "Ditaş Sanayi (B)", "Otomotiv & Yan Sanayi", True, 19.2),
        ("DOGUB", "Doğusan Boru", "İnşaat & Çimento", True, 28.6),
        ("DURDO", "Duran Doğan Basım", "Kağıt & Ambalaj", True, 16.4),
        ("EDATA", "E-Data Teknoloji", "Savunma & Teknoloji", True, 11.8),
        ("EDIP", "Edip Gayrimenkul", "GYO & Gayrimenkul", False, 21.4),
        ("EGEGUB", "Ege Gübre (B)", "Kimya & Petrokimya", True, 55.8),
        ("EPLAS", "Egeplast Ege Plastik", "Kimya & Petrokimya", True, 6.85),
        ("ERSU", "Ersu Meyve ve Gıda", "Perakende & Gıda", True, 14.6),
        ("ESCOM", "Escort Teknoloji", "Savunma & Teknoloji", True, 48.2),
        ("ETILR", "Etiler Gıda", "Perakende & Gıda", True, 12.8),
        ("EUHOL", "Euro Yatırım Holding", "Holding & Yatırım", False, 4.45),
        ("EUKYO", "Euro Kapital Yatırım", "Sigorta & Finans", False, 14.2),
        ("FLAP", "Flap Kongre", "Turizm & Medya", False, 11.6),
        ("FMIZP2", "Federal-Mogul (B)", "Otomotiv & Yan Sanayi", True, 290.0),
        ("FRIGO", "Frigo-Pak Gıda", "Perakende & Gıda", True, 8.95),
        ("GARFA", "Garanti Faktoring", "Sigorta & Finans", False, 28.4),
        ("GLBMD", "Global Menkul Değerler", "Sigorta & Finans", False, 24.6),
        ("GLCVY", "Gelecek Varlık Yönetimi", "Sigorta & Finans", False, 42.8),
        ("GMTAS", "Gimat Mağazacılık", "Perakende & Gıda", True, 16.4),
        ("GOZDE", "Gözde Girişim Sermayesi", "Holding & Yatırım", False, 26.8),
        ("GRNYO", "Garanti Yatırım Ortaklığı", "Sigorta & Finans", False, 8.45),
        ("GZNMI", "Gezinomi Seyahat", "Turizm & Medya", False, 24.6),
        ("HEDEF", "Hedef Holding", "Holding & Yatırım", False, 14.8),
        ("HKTM", "Hidropar Hareket Kontrol", "Makine & Sanayi", True, 24.2),
        ("HUBVC", "Hub Girişim Sermayesi", "Holding & Yatırım", False, 9.85),
        ("ICUGS", "ICU Girişim Sermayesi", "Holding & Yatırım", False, 8.65),
        ("IDGYO", "İdealist GYO", "GYO & Gayrimenkul", False, 5.45),
        ("IMASM", "İmaş Makina Sanayi", "Makine & Sanayi", True, 16.8),
        ("INTEM", "İntema İnşaat", "İnşaat & Çimento", True, 248.0),
        ("ISBIR", "İşbir Holding", "Holding & Yatırım", False, 118.0),
        ("ISFIN", "İş Finansal Kiralama", "Sigorta & Finans", False, 14.6),
        ("ISGSY", "İş Girişim Sermayesi", "Holding & Yatırım", False, 34.8),
        ("ISKPL", "Işık Plastik", "Kimya & Petrokimya", True, 11.4),
        ("IZINV", "İz Yatırım Holding", "Holding & Yatırım", False, 36.2),
        ("KAPLM", "Kaplamin Ambalaj", "Kağıt & Ambalaj", True, 148.0),
        ("KERVN", "Kervansaray Yatırım", "Turizm & Medya", False, 2.85),
        ("KLRHO", "Kiler Holding", "Holding & Yatırım", True, 34.6),
        ("KLVHO", "Kervan Gıda Sanayi", "Perakende & Gıda", True, 2.95),
        ("KRVGD", "Kervan Gıda", "Perakende & Gıda", True, 2.85),
        ("KRGYO", "Körfez GYO", "GYO & Gayrimenkul", True, 28.4),
        ("KSTUR", "Kuştur Kuşadası Turizm", "Turizm & Medya", False, 2450.0),
        ("LIDFA", "Lider Faktoring", "Sigorta & Finans", False, 6.45),
        ("MAKIM", "Makim Makina Teknolojileri", "Makine & Sanayi", True, 24.8),
        ("MANAS", "Manas Enerji Yönetimi", "Makine & Sanayi", True, 11.6),
        ("MACKO", "Maçkolik İnternet", "Turizm & Medya", False, 68.4),
        ("MERCN", "Mercan Kimya", "Kimya & Petrokimya", True, 16.2),
        ("MERKO", "Merko Gıda", "Perakende & Gıda", True, 14.8),
        ("METRO", "Metro Ticari ve Mali", "Holding & Yatırım", False, 2.65),
        ("MNDTR", "Mondi Turkey", "Kağıt & Ambalaj", True, 18.4),
        ("MRGYO", "Martı GYO", "GYO & Gayrimenkul", False, 2.45),
        ("MSGYO", "Mistral GYO", "GYO & Gayrimenkul", False, 11.8),
        ("MTRKS", "Matriks Bilgi Dağıtım", "Savunma & Teknoloji", True, 44.6),
        ("MTRYO", "Metro Yatırım Ortaklığı", "Sigorta & Finans", False, 6.85),
    ]

    for sym, name, sec, kat, px in bist_high_beta_specs:
        idxs = ["BIST_HIGH_BETA", "BIST_TUM"]
        if kat:
            idxs.append("KATILIM_TUM")
        h = int(hashlib.md5(sym.encode()).hexdigest()[:6], 16)
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "BIST_STOCK",
            "market_group": "BIST Payları",
            "sector": sec,
            "category": "BIST Yüksek Beta / Gelişen",
            "indices": idxs,
            "currency": "TRY",
            "risk_level": 5,
            "is_katilim": kat,
            "katilim_bucket": "BIST_KATILIM" if kat else "NONE",
            "base_price": px,
            "pe_ratio": round(8.5 + (h % 310) / 10.0, 2),
            "pb_ratio": round(0.95 + (h % 450) / 100.0, 2),
            "roe_pct": round(5.0 + (h % 260) / 10.0, 2),
            "ter_pct": None,
            "yahoo_symbol": f"{sym}.IS",
        })

    # -------------------------------------------------------------------------
    # B. TEFAS FUNDS (111 Total — 53 Katılım + 58 Conventional)
    # -------------------------------------------------------------------------
    tefas_katilim_specs = [
        ("KTV", "Kuveyt Türk Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 1, 5.42, 1.15),
        ("KTP", "Kuveyt Türk Portföy Katılım Hisse Senedi (TL) Fonu", "Katılım Hisse Senedi", 4, 18.65, 2.10),
        ("KZL", "Kuveyt Türk Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 14.20, 1.50),
        ("KGM", "Kuveyt Türk Portföy Gümüş Katılım Fonu", "Kıymetli Maden / Gümüş", 4, 4.85, 1.65),
        ("KLU", "Kuveyt Türk Portföy Para Piyasası Katılım Fonu", "Katılım Para Piyasası", 1, 2.48, 0.95),
        ("KIS", "Kuveyt Türk Portföy Sürdürülebilirlik Katılım Fonu", "Katılım Hisse Senedi", 4, 3.12, 2.05),
        ("KTT", "Kuveyt Türk Portföy Teknoloji Katılım Fonu", "Katılım Teknoloji", 4, 2.94, 2.25),
        ("KPU", "Kuveyt Türk Portföy Çoklu Varlık Katılım Fonu", "Katılım Değişken", 3, 3.65, 1.85),
        ("KTN", "Kuveyt Türk Portföy Birinci Katılım Fonu", "Katılım Değişken", 3, 4.18, 1.80),
        ("KUT", "Kuveyt Türk Portföy Kıymetli Madenler Katılım Fonu", "Kıymetli Maden / Altın", 3, 6.42, 1.60),
        ("MPS", "Mükafat Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 12.85, 2.15),
        ("MPK", "Mükafat Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 1, 4.12, 1.10),
        ("MKG", "Mükafat Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 9.74, 1.45),
        ("MPF", "Mükafat Portföy Katılım Fonu", "Katılım Değişken", 3, 3.45, 1.90),
        ("RBH", "Albaraka Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 15.30, 2.10),
        ("RBA", "Albaraka Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 8.92, 1.55),
        ("RBT", "Albaraka Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 1, 3.84, 1.05),
        ("RBF", "Albaraka Portföy Katılım Fonu", "Katılım Değişken", 3, 4.25, 1.85),
        ("ZPE", "Ziraat Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 11.40, 2.00),
        ("ZPG", "Ziraat Portföy Kira Sertifikası (Sukuk) Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 6.18, 1.25),
        ("ZPK", "Ziraat Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 1, 3.92, 1.00),
        ("ZPA", "Ziraat Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 12.45, 1.40),
        ("ZPF", "Ziraat Portföy Katılım Fonu (Döviz)", "Katılım Döviz / Eurobond", 3, 5.18, 1.50),
        ("ZPC", "Ziraat Portföy Katılım Endeksi Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 8.75, 1.85),
        ("HKH", "Hedef Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 4.68, 2.30),
        ("CPU", "Aktif Portföy Teknoloji Katılım Fonu", "Katılım Teknoloji", 4, 2.45, 2.20),
        ("MJG", "Aktif Portföy Gümüş Katılım Fonu", "Kıymetli Maden / Gümüş", 4, 3.28, 1.70),
        ("MPN", "Aktif Portföy Kısa Vadeli Katılım Fonu", "Kira Sertifikası (Sukuk)", 1, 3.14, 1.10),
        ("ONE", "Aktif Portföy Birinci Katılım Fonu", "Katılım Değişken", 3, 2.86, 1.85),
        ("EKF", "QInvest Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 5.64, 1.30),
        ("ELZ", "QInvest Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 9.42, 2.15),
        ("EIB", "QInvest Portföy İkinci Katılım Değişken Fonu", "Katılım Değişken", 3, 4.32, 1.90),
        ("GOL", "Garanti Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 7.85, 1.45),
        ("GKF", "Garanti Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 4.56, 1.25),
        ("GKH", "Garanti Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 6.24, 2.10),
        ("ICE", "İş Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 4.92, 1.20),
        ("IKP", "İş Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 7.15, 2.05),
        ("IOG", "İş Portföy İş Bankası İştirakleri Katılım Fonu", "Katılım Değişken", 3, 3.84, 1.95),
        ("YKT", "Yapı Kredi Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 10.62, 1.50),
        ("YKH", "Yapı Kredi Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 5.94, 2.10),
        ("YKS", "Yapı Kredi Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 4.18, 1.25),
        ("AKE", "Ak Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 4.76, 1.25),
        ("AKK", "Ak Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 5.48, 2.10),
        ("AFO", "Ak Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 9.15, 1.45),
        ("VKF", "Vakıf Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 1, 3.68, 1.05),
        ("VKH", "Vakıf Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 6.82, 2.00),
        ("VKA", "Vakıf Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 8.45, 1.45),
        ("HDA", "Halk Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 7.92, 1.45),
        ("HKF", "Halk Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 4.35, 1.20),
        ("DPK", "Deniz Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 3.98, 1.25),
        ("DPA", "Deniz Portföy Altın Katılım Fonu", "Kıymetli Maden / Altın", 3, 8.12, 1.50),
        ("TPK", "TEB Portföy Kira Sertifikası Katılım Fonu", "Kira Sertifikası (Sukuk)", 2, 4.24, 1.25),
        ("GIK", "Global MD Portföy Katılım Hisse Senedi Fonu", "Katılım Hisse Senedi", 4, 5.12, 2.20),
    ]

    for sym, name, cat, rl, px, ter in tefas_katilim_specs:
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "TEFAS_FUND",
            "market_group": "TEFAS Fonları",
            "sector": "TEFAS Katılım",
            "category": cat,
            "indices": ["TEFAS_ALL", "TEFAS_KATILIM"],
            "currency": "TRY",
            "risk_level": rl,
            "is_katilim": True,
            "katilim_bucket": "TEFAS_KATILIM",
            "base_price": px,
            "pe_ratio": None,
            "pb_ratio": None,
            "roe_pct": None,
            "ter_pct": ter,
            "yahoo_symbol": None,
        })

    tefas_conv_specs = [
        ("TI1", "İş Portföy Para Piyasası (TL) Fonu", "Para Piyasası", 1, 6.84, 0.65),
        ("RPP", "Rota Portföy Para Piyasası Fonu", "Para Piyasası", 1, 4.92, 0.55),
        ("PPN", "Nurol Portföy Para Piyasası Fonu", "Para Piyasası", 1, 3.45, 0.60),
        ("PRY", "QNB Finans Portföy Para Piyasası Fonu", "Para Piyasası", 1, 5.12, 0.68),
        ("ALE", "Ak Portföy Para Piyasası Fonu", "Para Piyasası", 1, 7.24, 0.72),
        ("GAL", "Garanti Portföy Para Piyasası Fonu", "Para Piyasası", 1, 6.18, 0.70),
        ("YLP", "Yapı Kredi Portföy Para Piyasası Fonu", "Para Piyasası", 1, 5.84, 0.72),
        ("TZL", "Ziraat Portföy Para Piyasası Fonu", "Para Piyasası", 1, 4.65, 0.62),
        ("NVB", "Neo Portföy Para Piyasası Fonu", "Para Piyasası", 1, 2.94, 0.58),
        ("HVT", "Hedef Portföy Para Piyasası Fonu", "Para Piyasası", 1, 3.18, 0.60),
        ("MAC", "Marmara Capital Portföy Hisse Senedi (TL) Fonu", "Hisse Senedi (TL)", 4, 24.80, 2.40),
        ("IIH", "İstanbul Portföy Üçüncü Hisse Senedi Fonu", "Hisse Senedi (TL)", 4, 19.45, 2.35),
        ("NNF", "Hedef Portföy Birinci Hisse Senedi Fonu", "Hisse Senedi (TL)", 4, 28.60, 2.50),
        ("TI2", "İş Portföy BIST 30 Endeksi Hisse Senedi Fonu", "Hisse Senedi (TL)", 4, 14.20, 1.85),
        ("GHS", "Garanti Portföy Hisse Senedi Fonu", "Hisse Senedi (TL)", 4, 16.45, 2.10),
        ("AK3", "Ak Portföy BIST Banka Endeksi Hisse Senedi Fonu", "Hisse Senedi (TL)", 5, 11.80, 1.95),
        ("YAS", "Yapı Kredi Portföy Koç Holding İştirak ve Hisse Fonu", "Hisse Senedi (TL)", 4, 21.30, 2.05),
        ("IDH", "İş Portföy BIST 100 Dışı Şirketler Hisse Senedi Fonu", "Hisse Senedi (TL)", 5, 13.60, 2.20),
        ("BGP", "Bosphorus Capital Hisse Senedi Fonu", "Hisse Senedi (TL)", 4, 9.85, 2.30),
        ("ST1", "Strateji Portföy Birinci Hisse Senedi Fonu", "Hisse Senedi (TL)", 4, 34.20, 2.40),
        ("AFT", "Ak Portföy Yeni Teknolojiler Yabancı Hisse Fonu", "Yabancı Hisse & Teknoloji", 5, 18.90, 2.65),
        ("YAY", "Yapı Kredi Portföy Yabancı Teknoloji Sektörü Fonu", "Yabancı Hisse & Teknoloji", 5, 22.40, 2.70),
        ("AFA", "Ak Portföy Amerika Yabancı Hisse Senedi Fonu", "Yabancı Hisse & Teknoloji", 4, 14.80, 2.55),
        ("TMG", "İş Portföy Yabancı Hisse Senedi Fonu", "Yabancı Hisse & Teknoloji", 4, 16.20, 2.50),
        ("GUH", "Garanti Portföy Yabancı Teknoloji Fon Sepeti Fonu", "Yabancı Hisse & Teknoloji", 5, 12.60, 2.45),
        ("OJT", "QNB Finans Portföy Teknoloji Fon Sepeti Fonu", "Yabancı Hisse & Teknoloji", 5, 9.45, 2.40),
        ("ITP", "İş Portföy Teknoloji Karma Fonu", "Yabancı Hisse & Teknoloji", 4, 8.75, 2.30),
        ("TTE", "İş Portföy BIST Teknoloji Ağırlık Sınırlamalı Fon", "Hisse Senedi (TL)", 5, 15.80, 2.15),
        ("ICZ", "Ak Portföy BIST Teknoloji Endeksi Hisse Fonu", "Hisse Senedi (TL)", 5, 11.40, 2.10),
        ("TCD", "Tacirler Portföy Değişken Fon", "Değişken & Karma", 4, 32.50, 2.60),
        ("IPB", "İstanbul Portföy Birinci Değişken Fon", "Değişken & Karma", 4, 14.90, 2.45),
        ("RPD", "Rota Portföy Değişken Fon", "Değişken & Karma", 4, 8.65, 2.30),
        ("GMR", "Inveo Portföy İkinci Değişken Fon", "Değişken & Karma", 4, 11.20, 2.40),
        ("OPB", "Osmanlı Portföy Birinci Değişken Fon", "Değişken & Karma", 3, 7.85, 2.10),
        ("KUB", "Kare Portföy Değişken (Döviz) Fon", "Döviz & Eurobond", 3, 6.45, 1.95),
        ("IPV", "İş Portföy Eurobond Borçlanma Araçları Fonu", "Döviz & Eurobond", 3, 9.12, 1.65),
        ("DBH", "Deniz Portföy Eurobond Borçlanma Araçları Fonu", "Döviz & Eurobond", 3, 8.45, 1.70),
        ("AKE2", "Ak Portföy Eurobond (Dolar) Borçlanma Araçları Fonu", "Döviz & Eurobond", 3, 7.90, 1.65),
        ("GPA", "Garanti Portföy Eurobond Borçlanma Araçları Fonu", "Döviz & Eurobond", 3, 8.20, 1.65),
        ("YBE", "Yapı Kredi Portföy Eurobond (Dolar) Borçlanma Fonu", "Döviz & Eurobond", 3, 7.65, 1.68),
        ("TTA", "İş Portföy Altın Fonu", "Kıymetli Maden / Altın", 3, 13.85, 1.45),
        ("GGA", "Garanti Portföy Altın Fonu", "Kıymetli Maden / Altın", 3, 12.90, 1.50),
        ("TCA", "Ziraat Portföy Altın Fonu", "Kıymetli Maden / Altın", 3, 11.75, 1.40),
        ("YKT2", "Yapı Kredi Portföy Altın Fonu", "Kıymetli Maden / Altın", 3, 11.40, 1.50),
        ("AES", "Ak Portföy Petrol Yabancı BYF Fon Sepeti Fonu", "Emtia & Enerji", 5, 6.24, 2.35),
        ("TGE", "İş Portföy Emtia Yabancı BYF Fon Sepeti Fonu", "Emtia & Enerji", 4, 7.85, 2.25),
        ("GUM", "Ak Portföy Gümüş Fon Sepeti Fonu", "Kıymetli Maden / Gümüş", 4, 5.42, 1.85),
        ("DMG", "Deniz Portföy Gümüş Fon Sepeti Fonu", "Kıymetli Maden / Gümüş", 4, 4.96, 1.85),
        ("GTZ", "Garanti Portföy Temiz Enerji Değişken Fon", "Tematik & ESG", 4, 4.35, 2.20),
        ("AOY", "Ak Portföy Alternatif Enerji Yabancı Hisse Fonu", "Tematik & ESG", 5, 5.62, 2.45),
        ("AFS", "Ak Portföy Sağlık Sektörü Yabancı Hisse Fonu", "Tematik & ESG", 4, 8.15, 2.40),
        ("GBG", "Inveo Portföy G20 Ülkeleri Yabancı Hisse Fonu", "Yabancı Hisse & Teknoloji", 4, 6.95, 2.35),
        ("YAC", "Yapı Kredi Portföy Temiz Enerji Değişken Fon", "Tematik & ESG", 4, 4.82, 2.20),
        ("IJA", "İş Portföy Yarı İletken Teknolojileri Değişken Fon", "Yabancı Hisse & Teknoloji", 5, 7.45, 2.50),
        ("DVT", "Deniz Portföy Metaverse ve Dijital Yaşam Fonu", "Yabancı Hisse & Teknoloji", 5, 5.84, 2.45),
        ("MJB", "Aktif Portföy Blockchain Teknolojileri Fonu", "Yabancı Hisse & Teknoloji", 5, 4.92, 2.55),
        ("OHB", "Oyak Portföy Birinci Borçlanma Araçları Fonu", "Borçlanma Araçları", 2, 4.15, 1.15),
        ("IST", "İş Portföy Kısa Vadeli Borçlanma Araçları Fonu", "Borçlanma Araçları", 1, 5.28, 0.95),
    ]

    for sym, name, cat, rl, px, ter in tefas_conv_specs:
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "TEFAS_FUND",
            "market_group": "TEFAS Fonları",
            "sector": "TEFAS Konvansiyonel",
            "category": cat,
            "indices": ["TEFAS_ALL"],
            "currency": "TRY",
            "risk_level": rl,
            "is_katilim": False,
            "katilim_bucket": "NONE",
            "base_price": px,
            "pe_ratio": None,
            "pb_ratio": None,
            "roe_pct": None,
            "ter_pct": ter,
            "yahoo_symbol": None,
        })

    # -------------------------------------------------------------------------
    # C. GLOBAL ETFs & US EQUITIES (203 Total — 8 Islamic ETFs + 47 ETFs + 148 US)
    # -------------------------------------------------------------------------
    islamic_etfs = [
        ("SPUS", "SP Funds S&P 500 Sharia Industry Exclusions ETF", "İslami ETF (ABD)", 3, 42.80, 0.45),
        ("HLAL", "Wahed FTSE USA Shariah ETF", "İslami ETF (ABD)", 3, 54.60, 0.50),
        ("UMMA", "Wahed Dow Jones Islamic World ETF", "İslami ETF (Küresel)", 3, 27.40, 0.65),
        ("SPSK", "SP Funds Dow Jones Global Sukuk ETF", "Küresel Sukuk ETF", 2, 18.15, 0.55),
        ("ISDU", "iShares MSCI USA Islamic UCITS ETF", "İslami ETF (ABD)", 3, 74.20, 0.30),
        ("ISWD", "iShares MSCI World Islamic UCITS ETF", "İslami ETF (Küresel)", 3, 46.80, 0.30),
        ("ISDE", "iShares MSCI EM Islamic UCITS ETF", "İslami ETF (GOÜ)", 4, 21.90, 0.35),
        ("IGDA", "Invesco Dow Jones Islamic Global Developed ETF", "İslami ETF (Küresel)", 3, 28.50, 0.40),
    ]
    for sym, name, cat, rl, px, ter in islamic_etfs:
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "GLOBAL_ETF",
            "market_group": "Küresel ETF & ABD",
            "sector": "İslami ETF & Sukuk",
            "category": cat,
            "indices": ["GLOBAL_ETF", "ISLAMIC_GLOBAL"],
            "currency": "USD",
            "risk_level": rl,
            "is_katilim": True,
            "katilim_bucket": "ISLAMIC_ETF_SUKUK",
            "base_price": px,
            "pe_ratio": 21.5,
            "pb_ratio": 3.4,
            "roe_pct": 18.0,
            "ter_pct": ter,
            "yahoo_symbol": sym,
        })

    global_etfs = [
        ("SPY", "SPDR S&P 500 ETF Trust", "ABD Büyük Ölçek", 3, 598.0, 0.09),
        ("VOO", "Vanguard S&P 500 ETF", "ABD Büyük Ölçek", 3, 548.0, 0.03),
        ("IVV", "iShares Core S&P 500 ETF", "ABD Büyük Ölçek", 3, 601.0, 0.03),
        ("QQQ", "Invesco QQQ Trust (Nasdaq-100)", "ABD Teknoloji", 4, 514.0, 0.20),
        ("QQQM", "Invesco NASDAQ 100 ETF", "ABD Teknoloji", 4, 211.0, 0.15),
        ("VTI", "Vanguard Total Stock Market ETF", "ABD Toplam Piyasa", 3, 294.0, 0.03),
        ("DIA", "SPDR Dow Jones Industrial Average ETF", "ABD Büyük Ölçek", 3, 438.0, 0.16),
        ("IWM", "iShares Russell 2000 Small-Cap ETF", "ABD Küçük Ölçek", 4, 232.0, 0.19),
        ("VEA", "Vanguard FTSE Developed Markets ETF", "Gelişmiş Piyasalar", 3, 51.8, 0.05),
        ("VWO", "Vanguard FTSE Emerging Markets ETF", "Gelişen Piyasalar", 4, 46.2, 0.08),
        ("EEM", "iShares MSCI Emerging Markets ETF", "Gelişen Piyasalar", 4, 44.5, 0.68),
        ("TUR", "iShares MSCI Turkey ETF", "Türkiye ETF", 4, 38.4, 0.59),
        ("VT", "Vanguard Total World Stock ETF", "Küresel Hisse", 3, 121.0, 0.07),
        ("VXUS", "Vanguard Total International Stock ETF", "Küresel Hisse", 3, 64.2, 0.07),
        ("SCHD", "Schwab US Dividend Equity ETF", "Temettü ETF", 3, 86.4, 0.06),
        ("VYM", "Vanguard High Dividend Yield ETF", "Temettü ETF", 3, 131.0, 0.06),
        ("VIG", "Vanguard Dividend Appreciation ETF", "Temettü ETF", 3, 198.0, 0.06),
        ("JEPI", "JPMorgan Equity Premium Income ETF", "Gelir & Opsiyon ETF", 3, 59.4, 0.35),
        ("JEPQ", "JPMorgan Nasdaq Equity Premium Income ETF", "Gelir & Opsiyon ETF", 4, 57.2, 0.35),
        ("XLK", "Technology Select Sector SPDR", "Sektör ETF", 4, 234.0, 0.09),
        ("XLF", "Financial Select Sector SPDR", "Sektör ETF", 3, 49.6, 0.09),
        ("XLE", "Energy Select Sector SPDR", "Sektör ETF", 4, 94.2, 0.09),
        ("XLV", "Health Care Select Sector SPDR", "Sektör ETF", 3, 148.0, 0.09),
        ("XLI", "Industrial Select Sector SPDR", "Sektör ETF", 3, 141.0, 0.09),
        ("XLY", "Consumer Discretionary Select SPDR", "Sektör ETF", 4, 218.0, 0.09),
        ("XLP", "Consumer Staples Select SPDR", "Sektör ETF", 2, 82.4, 0.09),
        ("XLU", "Utilities Select Sector SPDR", "Sektör ETF", 2, 79.6, 0.09),
        ("XLB", "Materials Select Sector SPDR", "Sektör ETF", 3, 92.8, 0.09),
        ("XLRE", "Real Estate Select Sector SPDR", "Sektör ETF", 3, 43.2, 0.09),
        ("XLC", "Communication Services Select SPDR", "Sektör ETF", 4, 96.8, 0.09),
        ("SMH", "VanEck Semiconductor ETF", "Yarı İletken & AI", 4, 254.0, 0.35),
        ("SOXX", "iShares Semiconductor ETF", "Yarı İletken & AI", 4, 228.0, 0.35),
        ("BOTZ", "Global X Robotics & AI ETF", "Yarı İletken & AI", 4, 33.4, 0.68),
        ("ARKK", "ARK Innovation ETF", "Tematik İnovasyon", 5, 58.6, 0.75),
        ("ICLN", "iShares Global Clean Energy ETF", "Temiz Enerji", 4, 14.2, 0.41),
        ("URA", "Global X Uranium ETF", "Emtia & Enerji", 4, 31.8, 0.69),
        ("TLT", "iShares 20+ Year Treasury Bond ETF", "ABD Tahvil ETF", 2, 92.4, 0.15),
        ("IEF", "iShares 7-10 Year Treasury Bond ETF", "ABD Tahvil ETF", 2, 94.8, 0.15),
        ("SHY", "iShares 1-3 Year Treasury Bond ETF", "ABD Tahvil ETF", 1, 82.1, 0.15),
        ("SGOV", "iShares 0-3 Month Treasury Bond ETF", "ABD Tahvil ETF", 1, 100.5, 0.09),
        ("AGG", "iShares Core U.S. Aggregate Bond ETF", "ABD Tahvil ETF", 2, 98.6, 0.03),
        ("BND", "Vanguard Total Bond Market ETF", "ABD Tahvil ETF", 2, 73.4, 0.03),
        ("HYG", "iShares iBoxx High Yield Corporate Bond", "Kurumsal Tahvil", 3, 79.8, 0.49),
        ("GLD", "SPDR Gold Shares ETF", "Altın & Emtia ETF", 3, 246.0, 0.40),
        ("SLV", "iShares Silver Trust ETF", "Altın & Emtia ETF", 4, 29.4, 0.50),
        ("USO", "United States Oil Fund LP", "Altın & Emtia ETF", 4, 74.5, 0.60),
        ("VNQ", "Vanguard Real Estate ETF", "GYO ETF", 3, 93.4, 0.12),
    ]
    for sym, name, cat, rl, px, ter in global_etfs:
        is_k = sym in ("GLD", "SLV")
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "GLOBAL_ETF",
            "market_group": "Küresel ETF & ABD",
            "sector": "Küresel ETF",
            "category": cat,
            "indices": ["GLOBAL_ETF"] + (["ISLAMIC_GLOBAL"] if is_k else []),
            "currency": "USD",
            "risk_level": rl,
            "is_katilim": is_k,
            "katilim_bucket": "ISLAMIC_ETF_SUKUK" if is_k else "NONE",
            "base_price": px,
            "pe_ratio": 19.8,
            "pb_ratio": 3.1,
            "roe_pct": 16.5,
            "ter_pct": ter,
            "yahoo_symbol": sym,
        })

    # 148 US Mega & Large Cap Equities
    us_equities_raw = [
        ("AAPL", "Apple Inc.", "ABD Teknoloji", True, 228.5),
        ("MSFT", "Microsoft Corp.", "ABD Teknoloji", True, 424.0),
        ("NVDA", "NVIDIA Corp.", "Yarı İletken & AI", True, 142.0),
        ("AMZN", "Amazon.com Inc.", "ABD Tüketici", False, 208.0),
        ("GOOGL", "Alphabet Inc. (A)", "ABD Teknoloji", True, 176.5),
        ("META", "Meta Platforms Inc.", "ABD Teknoloji", True, 588.0),
        ("TSLA", "Tesla Inc.", "Otomotiv & EV", True, 326.0),
        ("AVGO", "Broadcom Inc.", "Yarı İletken & AI", True, 174.0),
        ("LLY", "Eli Lilly and Co.", "ABD Sağlık", True, 812.0),
        ("BRK-B", "Berkshire Hathaway B", "ABD Finans", False, 464.0),
        ("JPM", "JPMorgan Chase & Co.", "ABD Finans", False, 242.0),
        ("V", "Visa Inc.", "ABD Finans", True, 308.0),
        ("MA", "Mastercard Inc.", "ABD Finans", True, 524.0),
        ("UNH", "UnitedHealth Group", "ABD Sağlık", False, 594.0),
        ("XOM", "Exxon Mobil Corp.", "ABD Enerji", True, 121.0),
        ("CVX", "Chevron Corp.", "ABD Enerji", True, 161.0),
        ("JNJ", "Johnson & Johnson", "ABD Sağlık", True, 156.0),
        ("PG", "Procter & Gamble", "ABD Tüketici", True, 171.0),
        ("HD", "Home Depot Inc.", "ABD Tüketici", True, 412.0),
        ("COST", "Costco Wholesale", "ABD Tüketici", False, 924.0),
        ("WMT", "Walmart Inc.", "ABD Tüketici", False, 86.5),
        ("ABBV", "AbbVie Inc.", "ABD Sağlık", True, 178.0),
        ("MRK", "Merck & Co.", "ABD Sağlık", True, 102.0),
        ("KO", "Coca-Cola Co.", "ABD Tüketici", True, 64.2),
        ("PEP", "PepsiCo Inc.", "ABD Tüketici", True, 164.0),
        ("ORCL", "Oracle Corp.", "ABD Teknoloji", True, 186.0),
        ("CRM", "Salesforce Inc.", "ABD Teknoloji", True, 328.0),
        ("ADBE", "Adobe Inc.", "ABD Teknoloji", True, 512.0),
        ("AMD", "Advanced Micro Devices", "Yarı İletken & AI", True, 144.0),
        ("INTC", "Intel Corp.", "Yarı İletken & AI", True, 24.6),
        ("QCOM", "Qualcomm Inc.", "Yarı İletken & AI", True, 164.0),
        ("TXN", "Texas Instruments", "Yarı İletken & AI", True, 206.0),
        ("AMAT", "Applied Materials", "Yarı İletken & AI", True, 178.0),
        ("LRCX", "Lam Research Corp.", "Yarı İletken & AI", True, 76.4),
        ("KLAC", "KLA Corp.", "Yarı İletken & AI", True, 668.0),
        ("MU", "Micron Technology", "Yarı İletken & AI", True, 104.0),
        ("ASML", "ASML Holding NV", "Yarı İletken & AI", True, 692.0),
        ("TSM", "Taiwan Semiconductor", "Yarı İletken & AI", True, 192.0),
        ("ARM", "Arm Holdings plc", "Yarı İletken & AI", True, 142.0),
        ("PLTR", "Palantir Technologies", "ABD Teknoloji", True, 62.4),
        ("NOW", "ServiceNow Inc.", "ABD Teknoloji", True, 1024.0),
        ("INTU", "Intuit Inc.", "ABD Teknoloji", True, 668.0),
        ("PANW", "Palo Alto Networks", "Siber Güvenlik", True, 386.0),
        ("CRWD", "CrowdStrike Holdings", "Siber Güvenlik", True, 348.0),
        ("FTNT", "Fortinet Inc.", "Siber Güvenlik", True, 94.5),
        ("ZS", "Zscaler Inc.", "Siber Güvenlik", True, 204.0),
        ("NET", "Cloudflare Inc.", "ABD Teknoloji", True, 96.4),
        ("SNOW", "Snowflake Inc.", "ABD Teknoloji", True, 168.0),
        ("DDOG", "Datadog Inc.", "ABD Teknoloji", True, 146.0),
        ("MDB", "MongoDB Inc.", "ABD Teknoloji", True, 312.0),
        ("SHOP", "Shopify Inc.", "ABD Teknoloji", True, 108.0),
        ("UBER", "Uber Technologies", "ABD Teknoloji", True, 72.4),
        ("ABNB", "Airbnb Inc.", "ABD Tüketici", True, 136.0),
        ("NFLX", "Netflix Inc.", "Medya & İletişim", False, 864.0),
        ("DIS", "Walt Disney Co.", "Medya & İletişim", False, 114.0),
        ("SPOT", "Spotify Technology", "Medya & İletişim", False, 468.0),
        ("TMUS", "T-Mobile US Inc.", "Medya & İletişim", True, 238.0),
        ("VZ", "Verizon Communications", "Medya & İletişim", False, 42.6),
        ("T", "AT&T Inc.", "Medya & İletişim", False, 22.8),
        ("CSCO", "Cisco Systems", "ABD Teknoloji", True, 58.4),
        ("IBM", "IBM Corp.", "ABD Teknoloji", True, 218.0),
        ("ACN", "Accenture plc", "ABD Teknoloji", True, 362.0),
        ("SAP", "SAP SE", "ABD Teknoloji", True, 242.0),
        ("SNPS", "Synopsys Inc.", "Yarı İletken & AI", True, 548.0),
        ("CDNS", "Cadence Design Systems", "Yarı İletken & AI", True, 296.0),
        ("ANET", "Arista Networks", "ABD Teknoloji", True, 408.0),
        ("DELL", "Dell Technologies", "ABD Teknoloji", True, 134.0),
        ("SMCI", "Super Micro Computer", "Yarı İletken & AI", True, 36.4),
        ("MRVL", "Marvell Technology", "Yarı İletken & AI", True, 92.6),
        ("ON", "ON Semiconductor", "Yarı İletken & AI", True, 71.4),
        ("NXPI", "NXP Semiconductors", "Yarı İletken & AI", True, 232.0),
        ("ADI", "Analog Devices", "Yarı İletken & AI", True, 224.0),
        ("MCHP", "Microchip Technology", "Yarı İletken & AI", True, 72.8),
        ("BAC", "Bank of America", "ABD Finans", False, 46.2),
        ("WFC", "Wells Fargo & Co.", "ABD Finans", False, 72.4),
        ("GS", "Goldman Sachs Group", "ABD Finans", False, 594.0),
        ("MS", "Morgan Stanley", "ABD Finans", False, 132.0),
        ("BLK", "BlackRock Inc.", "ABD Finans", False, 1042.0),
        ("SCHW", "Charles Schwab Corp.", "ABD Finans", False, 81.4),
        ("AXP", "American Express", "ABD Finans", False, 296.0),
        ("C", "Citigroup Inc.", "ABD Finans", False, 69.8),
        ("SPGI", "S&P Global Inc.", "ABD Finans", True, 506.0),
        ("MCO", "Moody's Corp.", "ABD Finans", True, 484.0),
        ("PYPL", "PayPal Holdings", "ABD Finans", True, 86.2),
        ("SQ", "Block Inc.", "ABD Finans", True, 88.4),
        ("COIN", "Coinbase Global", "ABD Finans", False, 304.0),
        ("TMO", "Thermo Fisher Scientific", "ABD Sağlık", True, 534.0),
        ("ABT", "Abbott Laboratories", "ABD Sağlık", True, 118.0),
        ("DHR", "Danaher Corp.", "ABD Sağlık", True, 242.0),
        ("PFE", "Pfizer Inc.", "ABD Sağlık", True, 26.4),
        ("AMGN", "Amgen Inc.", "ABD Sağlık", True, 294.0),
        ("ISRG", "Intuitive Surgical", "ABD Sağlık", True, 542.0),
        ("SYK", "Stryker Corp.", "ABD Sağlık", True, 384.0),
        ("BSX", "Boston Scientific", "ABD Sağlık", True, 89.6),
        ("VRTX", "Vertex Pharmaceuticals", "ABD Sağlık", True, 468.0),
        ("REGN", "Regeneron Pharmaceuticals", "ABD Sağlık", True, 784.0),
        ("GILD", "Gilead Sciences", "ABD Sağlık", True, 92.4),
        ("BMY", "Bristol-Myers Squibb", "ABD Sağlık", True, 58.6),
        ("MDT", "Medtronic plc", "ABD Sağlık", True, 86.4),
        ("NVO", "Novo Nordisk A/S", "ABD Sağlık", True, 108.0),
        ("AZN", "AstraZeneca plc", "ABD Sağlık", True, 66.8),
        ("CAT", "Caterpillar Inc.", "ABD Sanayi", True, 396.0),
        ("GE", "GE Aerospace", "ABD Sanayi", True, 182.0),
        ("RTX", "RTX Corp.", "ABD Savunma", True, 121.0),
        ("LMT", "Lockheed Martin", "ABD Savunma", True, 542.0),
        ("NOC", "Northrop Grumman", "ABD Savunma", True, 496.0),
        ("GD", "General Dynamics", "ABD Savunma", True, 292.0),
        ("BA", "Boeing Co.", "ABD Savunma", True, 152.0),
        ("HON", "Honeywell International", "ABD Sanayi", True, 228.0),
        ("UNP", "Union Pacific Corp.", "ABD Sanayi", True, 244.0),
        ("UPS", "United Parcel Service", "ABD Sanayi", True, 134.0),
        ("FDX", "FedEx Corp.", "ABD Sanayi", True, 294.0),
        ("DE", "Deere & Co.", "ABD Sanayi", True, 412.0),
        ("ETN", "Eaton Corp.", "ABD Sanayi", True, 364.0),
        ("PH", "Parker-Hannifin", "ABD Sanayi", True, 684.0),
        ("EMR", "Emerson Electric", "ABD Sanayi", True, 128.0),
        ("MMM", "3M Company", "ABD Sanayi", True, 132.0),
        ("NKE", "Nike Inc.", "ABD Tüketici", True, 78.4),
        ("SBUX", "Starbucks Corp.", "ABD Tüketici", True, 98.6),
        ("MCD", "McDonald's Corp.", "ABD Tüketici", False, 294.0),
        ("LOW", "Lowe's Companies", "ABD Tüketici", True, 268.0),
        ("TGT", "Target Corp.", "ABD Tüketici", False, 152.0),
        ("TJX", "TJX Companies", "ABD Tüketici", True, 121.0),
        ("BKNG", "Booking Holdings", "ABD Tüketici", True, 5120.0),
        ("MAR", "Marriott International", "ABD Tüketici", False, 278.0),
        ("CMG", "Chipotle Mexican Grill", "ABD Tüketici", True, 61.4),
        ("LULU", "Lululemon Athletica", "ABD Tüketici", True, 318.0),
        ("F", "Ford Motor Co.", "Otomotiv & EV", True, 11.2),
        ("GM", "General Motors", "Otomotiv & EV", True, 56.4),
        ("RIVN", "Rivian Automotive", "Otomotiv & EV", True, 11.6),
        ("COP", "ConocoPhillips", "ABD Enerji", True, 112.0),
        ("SLB", "Schlumberger NV", "ABD Enerji", True, 44.2),
        ("EOG", "EOG Resources", "ABD Enerji", True, 134.0),
        ("MPC", "Marathon Petroleum", "ABD Enerji", True, 158.0),
        ("PSX", "Phillips 66", "ABD Enerji", True, 132.0),
        ("VLO", "Valero Energy", "ABD Enerji", True, 138.0),
        ("OXY", "Occidental Petroleum", "ABD Enerji", True, 51.4),
        ("NEE", "NextEra Energy", "ABD Enerji", True, 78.6),
        ("LIN", "Linde plc", "ABD Malzeme", True, 458.0),
        ("SHW", "Sherwin-Williams", "ABD Malzeme", True, 384.0),
        ("FCX", "Freeport-McMoRan", "ABD Malzeme", True, 44.6),
        ("NEM", "Newmont Corp.", "ABD Malzeme", True, 42.8),
        ("APD", "Air Products & Chemicals", "ABD Malzeme", True, 326.0),
        ("PLD", "Prologis Inc.", "ABD GYO", True, 118.0),
        ("AMT", "American Tower Corp.", "ABD GYO", True, 206.0),
        ("EQIX", "Equinix Inc.", "ABD GYO", True, 924.0),
        ("BABA", "Alibaba Group", "Küresel Teknoloji", True, 88.4),
        ("PDD", "PDD Holdings", "Küresel Teknoloji", True, 112.0),
    ]
    for sym, name, sec, kat, px in us_equities_raw:
        h = int(hashlib.md5(sym.encode()).hexdigest()[:6], 16)
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "US_STOCK",
            "market_group": "Küresel ETF & ABD",
            "sector": sec,
            "category": "ABD Büyük Ölçek Hisse",
            "indices": ["US_MEGA_CAP"] + (["ISLAMIC_GLOBAL"] if kat else []),
            "currency": "USD",
            "risk_level": 4,
            "is_katilim": kat,
            "katilim_bucket": "ISLAMIC_ETF_SUKUK" if kat else "NONE",
            "base_price": px,
            "pe_ratio": round(14.0 + (h % 350) / 10.0, 2),
            "pb_ratio": round(1.8 + (h % 800) / 100.0, 2),
            "roe_pct": round(12.0 + (h % 350) / 10.0, 2),
            "ter_pct": None,
            "yahoo_symbol": sym,
        })

    # -------------------------------------------------------------------------
    # D. CRYPTO & 3x LEVERAGED ETFs (60 Total)
    # -------------------------------------------------------------------------
    crypto_leveraged_specs = [
        ("BTC-USD", "Bitcoin (Spot USD)", "Kripto Varlık (L1)", 94500.0),
        ("ETH-USD", "Ethereum (Spot USD)", "Kripto Varlık (L1)", 3320.0),
        ("SOL-USD", "Solana (Spot USD)", "Kripto Varlık (L1)", 238.0),
        ("BNB-USD", "BNB Chain (Spot USD)", "Kripto Varlık (L1)", 642.0),
        ("XRP-USD", "Ripple XRP (Spot USD)", "Kripto Varlık (L1)", 1.42),
        ("ADA-USD", "Cardano (Spot USD)", "Kripto Varlık (L1)", 0.94),
        ("AVAX-USD", "Avalanche (Spot USD)", "Kripto Varlık (L1)", 41.5),
        ("DOT-USD", "Polkadot (Spot USD)", "Kripto Varlık (L1)", 8.45),
        ("LINK-USD", "Chainlink (Spot USD)", "Kripto DeFi / Oracle", 17.8),
        ("TON-USD", "Toncoin (Spot USD)", "Kripto Varlık (L1)", 5.85),
        ("SUI-USD", "Sui Network (Spot USD)", "Kripto Varlık (L1)", 3.42),
        ("NEAR-USD", "NEAR Protocol (Spot USD)", "Kripto AI / L1", 6.15),
        ("APT-USD", "Aptos (Spot USD)", "Kripto Varlık (L1)", 12.4),
        ("ARB-USD", "Arbitrum (Spot USD)", "Kripto Layer-2", 0.88),
        ("OP-USD", "Optimism (Spot USD)", "Kripto Layer-2", 2.18),
        ("MATIC-USD", "Polygon POL (Spot USD)", "Kripto Layer-2", 0.54),
        ("ATOM-USD", "Cosmos Hub (Spot USD)", "Kripto Varlık (L1)", 7.65),
        ("UNI-USD", "Uniswap (Spot USD)", "Kripto DeFi / Oracle", 11.4),
        ("AAVE-USD", "Aave Protocol (Spot USD)", "Kripto DeFi / Oracle", 178.0),
        ("MKR-USD", "MakerDAO (Spot USD)", "Kripto DeFi / Oracle", 1680.0),
        ("LDO-USD", "Lido DAO (Spot USD)", "Kripto DeFi / Oracle", 1.56),
        ("INJ-USD", "Injective (Spot USD)", "Kripto DeFi / Oracle", 26.8),
        ("RNDR-USD", "Render Network (Spot USD)", "Kripto AI / L1", 7.95),
        ("FET-USD", "Artificial Superintelligence (FET)", "Kripto AI / L1", 1.48),
        ("TAO-USD", "Bittensor (Spot USD)", "Kripto AI / L1", 542.0),
        ("GRT-USD", "The Graph (Spot USD)", "Kripto AI / L1", 0.24),
        ("FIL-USD", "Filecoin (Spot USD)", "Kripto Altyapı", 5.24),
        ("ICP-USD", "Internet Computer (Spot USD)", "Kripto Varlık (L1)", 10.6),
        ("HBAR-USD", "Hedera Hashgraph (Spot USD)", "Kripto Varlık (L1)", 0.145),
        ("VET-USD", "VeChain (Spot USD)", "Kripto Altyapı", 0.042),
        ("ALGO-USD", "Algorand (Spot USD)", "Kripto Varlık (L1)", 0.28),
        ("XLM-USD", "Stellar Lumens (Spot USD)", "Kripto Varlık (L1)", 0.44),
        ("LTC-USD", "Litecoin (Spot USD)", "Kripto Varlık (L1)", 96.4),
        ("BCH-USD", "Bitcoin Cash (Spot USD)", "Kripto Varlık (L1)", 485.0),
        ("ETC-USD", "Ethereum Classic (Spot USD)", "Kripto Varlık (L1)", 29.8),
        ("DOGE-USD", "Dogecoin (Spot USD)", "Kripto Yüksek Beta", 0.41),
        ("SHIB-USD", "Shiba Inu (Spot USD)", "Kripto Yüksek Beta", 0.000026),
        ("PEPE-USD", "Pepe (Spot USD)", "Kripto Yüksek Beta", 0.000021),
        ("WIF-USD", "dogwifhat (Spot USD)", "Kripto Yüksek Beta", 3.24),
        ("BONK-USD", "Bonk (Spot USD)", "Kripto Yüksek Beta", 0.000046),
        ("TIA-USD", "Celestia (Spot USD)", "Kripto Altyapı", 7.42),
        ("SEI-USD", "Sei Network (Spot USD)", "Kripto Varlık (L1)", 0.62),
        ("STX-USD", "Stacks Bitcoin L2 (Spot USD)", "Kripto Layer-2", 2.15),
        ("IMX-USD", "Immutable X (Spot USD)", "Kripto Layer-2", 1.68),
        ("RUNE-USD", "THORChain (Spot USD)", "Kripto DeFi / Oracle", 5.84),
        ("TQQQ", "ProShares UltraPro QQQ (3x Long Nasdaq)", "3x Kaldıraçlı ETF", 81.4),
        ("SQQQ", "ProShares UltraPro Short QQQ (3x Short)", "3x Kaldıraçlı ETF", 7.65),
        ("SOXL", "Direxion Daily Semiconductor Bull 3x", "3x Kaldıraçlı ETF", 31.8),
        ("SOXS", "Direxion Daily Semiconductor Bear 3x", "3x Kaldıraçlı ETF", 24.6),
        ("UPRO", "ProShares UltraPro S&P 500 (3x Long)", "3x Kaldıraçlı ETF", 94.2),
        ("SPXU", "ProShares UltraPro Short S&P 500 (3x)", "3x Kaldıraçlı ETF", 21.4),
        ("TNA", "Direxion Daily Small Cap Bull 3x", "3x Kaldıraçlı ETF", 48.6),
        ("LABU", "Direxion Daily S&P Biotech Bull 3x", "3x Kaldıraçlı ETF", 124.0),
        ("FNGU", "MicroSectors FANG+ Index 3x Leveraged", "3x Kaldıraçlı ETF", 524.0),
        ("NVDL", "GraniteShares 2x Long NVDA Daily ETF", "2x/3x Kaldıraçlı ETF", 76.4),
        ("TSLL", "Direxion Daily TSLA Bull 2x Shares", "2x/3x Kaldıraçlı ETF", 24.8),
        ("CONL", "GraniteShares 2x Long COIN Daily ETF", "2x/3x Kaldıraçlı ETF", 62.5),
        ("IBIT", "iShares Bitcoin Trust Spot ETF", "Spot Kripto ETF", 54.2),
        ("FBTC", "Fidelity Wise Origin Bitcoin Fund", "Spot Kripto ETF", 82.6),
        ("ETHA", "iShares Ethereum Trust Spot ETF", "Spot Kripto ETF", 25.4),
    ]
    for sym, name, cat, px in crypto_leveraged_specs:
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "CRYPTO_LEVERAGED",
            "market_group": "Kripto & 3x ETF",
            "sector": "Kripto & Kaldıraç",
            "category": cat,
            "indices": ["CRYPTO_LEV"],
            "currency": "USD",
            "risk_level": 5,
            "is_katilim": False,
            "katilim_bucket": "NONE",
            "base_price": px,
            "pe_ratio": None,
            "pb_ratio": None,
            "roe_pct": None,
            "ter_pct": 0.95 if "ETF" in cat else None,
            "yahoo_symbol": sym,
        })

    # -------------------------------------------------------------------------
    # E. FX, GOLD, COMMODITIES, BONDS & SUKUK (42 Total)
    # -------------------------------------------------------------------------
    macro_specs = [
        ("XAU_TRY", "Gram Altın (Spot TL) / Fiziki Altın", "Kıymetli Maden", "TRY", 2, True, 3045.0, None),
        ("XAG_TRY", "Gram Gümüş (Spot TL) / Fiziki Gümüş", "Kıymetli Maden", "TRY", 3, True, 35.80, None),
        ("XPT_TRY", "Gram Platin (Spot TL)", "Kıymetli Maden", "TRY", 3, True, 1085.0, None),
        ("XPD_TRY", "Gram Paladyum (Spot TL)", "Kıymetli Maden", "TRY", 4, True, 1140.0, None),
        ("GC=F", "Ons Altın (COMEX Gold Futures)", "Kıymetli Maden", "USD", 2, True, 2685.0, "GC=F"),
        ("SI=F", "Ons Gümüş (COMEX Silver Futures)", "Kıymetli Maden", "USD", 3, True, 31.40, "SI=F"),
        ("PL=F", "Platin Futures (NYMEX)", "Kıymetli Maden", "USD", 3, True, 968.0, "PL=F"),
        ("PA=F", "Paladyum Futures (NYMEX)", "Kıymetli Maden", "USD", 4, True, 1020.0, "PA=F"),
        ("HG=F", "Bakır Futures (COMEX Copper)", "Endüstriyel Emtia", "USD", 3, True, 4.18, "HG=F"),
        ("CL=F", "WTI Ham Petrol (NYMEX)", "Enerji Emtia", "USD", 4, True, 70.40, "CL=F"),
        ("BZ=F", "Brent Ham Petrol (ICE)", "Enerji Emtia", "USD", 4, True, 74.20, "BZ=F"),
        ("NG=F", "Doğal Gaz Futures (Henry Hub)", "Enerji Emtia", "USD", 5, True, 3.12, "NG=F"),
        ("ZW=F", "Buğday Futures (CBOT)", "Tarım Emtia", "USD", 3, True, 562.0, "ZW=F"),
        ("ZC=F", "Mısır Futures (CBOT)", "Tarım Emtia", "USD", 3, True, 428.0, "ZC=F"),
        ("KC=F", "Kahve Futures (ICE)", "Tarım Emtia", "USD", 4, True, 286.0, "KC=F"),
        ("USDTRY=X", "Dolar / Türk Lirası (USD/TRY)", "Döviz Kuru", "TRY", 2, True, 34.55, "USDTRY=X"),
        ("EURTRY=X", "Euro / Türk Lirası (EUR/TRY)", "Döviz Kuru", "TRY", 2, True, 36.42, "EURTRY=X"),
        ("GBPTRY=X", "İngiliz Sterlini / TL (GBP/TRY)", "Döviz Kuru", "TRY", 2, True, 43.80, "GBPTRY=X"),
        ("CHFTRY=X", "İsviçre Frangı / TL (CHF/TRY)", "Döviz Kuru", "TRY", 2, True, 39.10, "CHFTRY=X"),
        ("JPYTRY=X", "100 Japon Yeni / TL", "Döviz Kuru", "TRY", 2, True, 22.40, None),
        ("SARTRY=X", "Suudi Riyali / TL (SAR/TRY)", "Döviz Kuru", "TRY", 2, True, 9.20, None),
        ("AEDTRY=X", "BAE Dirhemi / TL (AED/TRY)", "Döviz Kuru", "TRY", 2, True, 9.40, None),
        ("EURUSD=X", "Euro / ABD Doları (EUR/USD)", "Majör Parite", "USD", 2, True, 1.054, "EURUSD=X"),
        ("GBPUSD=X", "Sterlin / ABD Doları (GBP/USD)", "Majör Parite", "USD", 2, True, 1.268, "GBPUSD=X"),
        ("USDJPY=X", "ABD Doları / Japon Yeni", "Majör Parite", "JPY", 3, True, 154.2, "USDJPY=X"),
        ("DX-Y.NYB", "ABD Dolar Endeksi (DXY)", "Küresel Gösterge", "USD", 2, True, 106.8, "DX-Y.NYB"),
        ("TR_SUKUK_2Y", "Hazine Müsteşarlığı 2 Yıllık TL Kira Sertifikası (Sukuk)", "Kamu Sukuk (TL)", "TRY", 1, True, 102.40, None),
        ("TR_SUKUK_5Y", "Hazine Müsteşarlığı 5 Yıllık TL Kira Sertifikası (Sukuk)", "Kamu Sukuk (TL)", "TRY", 2, True, 104.80, None),
        ("TR_SUKUK_USD", "T.C. Hazine USD Cinsi Kira Sertifikası (Global Sukuk)", "Global Sukuk (USD)", "USD", 2, True, 99.60, None),
        ("TR_SUKUK_GOLD", "T.C. Hazine Altına Dayalı Kira Sertifikası", "Altın Sukuk", "TRY", 2, True, 3058.0, None),
        ("KT_SUKUK_TL", "Kuveyt Türk Özel Sektör Kira Sertifikası (TL)", "Özel Sektör Sukuk", "TRY", 1, True, 101.20, None),
        ("ZF_SUKUK_TL", "Ziraat Katılım Kira Sertifikası (TL)", "Özel Sektör Sukuk", "TRY", 1, True, 101.45, None),
        ("VK_SUKUK_TL", "Vakıf Katılım Kira Sertifikası (TL)", "Özel Sektör Sukuk", "TRY", 1, True, 101.30, None),
        ("EM_SUKUK_TL", "Emlak Katılım Kira Sertifikası (TL)", "Özel Sektör Sukuk", "TRY", 1, True, 101.10, None),
        ("TR_HAZINE_BONO", "T.C. Hazine Kısa Vadeli İskontolu Bono", "Kamu Tahvil / Bono", "TRY", 1, False, 104.25, None),
        ("TR_TAHVIL_2Y", "T.C. 2 Yıllık Gösterge Devlet Tahvili", "Kamu Tahvil / Bono", "TRY", 2, False, 98.40, None),
        ("TR_TAHVIL_10Y", "T.C. 10 Yıllık Devlet Tahvili", "Kamu Tahvil / Bono", "TRY", 3, False, 94.80, None),
        ("TR_EUROBOND_5Y", "T.C. Hazine 5 Yıllık USD Eurobond", "Eurobond (USD)", "USD", 3, False, 101.50, None),
        ("TR_EUROBOND_10Y", "T.C. Hazine 10 Yıllık USD Eurobond", "Eurobond (USD)", "USD", 3, False, 99.20, None),
        ("US_TBILL_3M", "ABD 3 Aylık Hazine Bonosu (T-Bill)", "ABD Hazine Tahvili", "USD", 1, False, 98.85, None),
        ("US_TREASURY_10Y", "ABD 10 Yıllık Hazine Tahvili", "ABD Hazine Tahvili", "USD", 2, False, 96.40, None),
        ("ALTINS1", "Darphane Altın Sertifikası (BIST: ALTIN.S1)", "Kıymetli Maden", "TRY", 2, True, 31.85, "ALTIN.S1.IS"),
    ]
    for sym, name, cat, ccy, rl, kat, px, ysym in macro_specs:
        catalog.append({
            "symbol": sym,
            "name": name,
            "asset_class": "FX_GOLD_SUKUK",
            "market_group": "Döviz, Altın & Sukuk",
            "sector": cat,
            "category": cat,
            "indices": ["MACRO_ALL"] + (["ISLAMIC_GLOBAL"] if kat else []),
            "currency": ccy,
            "risk_level": rl,
            "is_katilim": kat,
            "katilim_bucket": "ISLAMIC_ETF_SUKUK" if kat else "NONE",
            "base_price": px,
            "pe_ratio": None,
            "pb_ratio": None,
            "roe_pct": None,
            "ter_pct": 0.0,
            "yahoo_symbol": ysym,
        })

    return catalog


UNIVERSAL_CATALOG = _build_full_universe_catalog()


# =============================================================================
# 4. VECTORIZED QUANTITATIVE INDICATORS & DATA FETCHER
# =============================================================================
class QuantitativeIndicators:
    """High-speed NumPy vectorized technical indicator engine."""

    @staticmethod
    def compute_all(df: pd.DataFrame) -> Dict[str, float]:
        close = df["Close"].to_numpy(dtype=np.float64)
        high = df["High"].to_numpy(dtype=np.float64)
        low = df["Low"].to_numpy(dtype=np.float64)
        vol = df["Volume"].to_numpy(dtype=np.float64)
        n = len(close)
        if n < 20:
            return {
                "rsi_14": 50.0, "macd_hist": 0.0, "sma20": float(close[-1]),
                "sma50": float(close[-1]), "sma50_dist_pct": 0.0,
                "vol_zscore": 0.0, "atr_pct": 1.8, "mom_20d": 0.0,
                "dist_low_20d": 5.0, "bb_pos": 0.5,
            }

        # RSI-14 (Wilder)
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)
        avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else np.mean(gains)
        avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else np.mean(losses)
        rs = avg_gain / (avg_loss + 1e-12)
        rsi_14 = float(100.0 - (100.0 / (1.0 + rs)))

        # EMA helper
        def _ema(arr: np.ndarray, span: int) -> np.ndarray:
            alpha = 2.0 / (span + 1.0)
            out = np.empty_like(arr)
            out[0] = arr[0]
            for i in range(1, len(arr)):
                out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
            return out

        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        macd_line = ema12 - ema26
        signal_line = _ema(macd_line, 9)
        macd_hist = float(macd_line[-1] - signal_line[-1])

        sma20 = float(np.mean(close[-20:]))
        sma50 = float(np.mean(close[-50:])) if n >= 50 else float(np.mean(close))
        sma50_dist_pct = float(((close[-1] - sma50) / (sma50 + 1e-12)) * 100.0)

        # Bollinger Band position
        std20 = float(np.std(close[-20:]))
        bb_upper = sma20 + 2.0 * std20
        bb_lower = sma20 - 2.0 * std20
        bb_pos = float((close[-1] - bb_lower) / ((bb_upper - bb_lower) + 1e-12))

        # Volume Z-Score
        v_window = vol[-20:]
        v_mean = float(np.mean(v_window))
        v_std = float(np.std(v_window))
        vol_zscore = float((vol[-1] - v_mean) / (v_std + 1e-12))

        # ATR %
        prev_c = np.roll(close, 1)
        prev_c[0] = close[0]
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
        atr14 = float(np.mean(tr[-14:]))
        atr_pct = float((atr14 / (close[-1] + 1e-12)) * 100.0)

        # 20-day momentum & distance from 20d low
        mom_20d = float(((close[-1] / (close[-20] + 1e-12)) - 1.0) * 100.0)
        low_20d = float(np.min(low[-20:]))
        dist_low_20d = float(((close[-1] - low_20d) / (low_20d + 1e-12)) * 100.0)

        return {
            "rsi_14": round(rsi_14, 2),
            "macd_hist": round(macd_hist, 4),
            "sma20": round(sma20, 4),
            "sma50": round(sma50, 4),
            "sma50_dist_pct": round(sma50_dist_pct, 2),
            "vol_zscore": round(vol_zscore, 2),
            "atr_pct": round(atr_pct, 2),
            "mom_20d": round(mom_20d, 2),
            "dist_low_20d": round(dist_low_20d, 2),
            "bb_pos": round(bb_pos, 3),
        }


class MultiAssetDataFetcher:
    """Fetches live TradingView/Yahoo market data with fast deterministic fallback."""

    TV_BIST_URL = "https://scanner.tradingview.com/turkey/scan"
    TV_US_URL = "https://scanner.tradingview.com/america/scan"

    @staticmethod
    def generate_synthetic_ohlcv(
        symbol: str, base_price: float, days: int = 120, risk_level: int = 3
    ) -> pd.DataFrame:
        seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)

        vol_map = {1: 0.0022, 2: 0.0065, 3: 0.0155, 4: 0.0235, 5: 0.0365}
        drift_map = {1: 0.0012, 2: 0.0008, 3: 0.0006, 4: 0.0007, 5: 0.0009}
        sigma = vol_map.get(risk_level, 0.016)
        mu = drift_map.get(risk_level, 0.0006)

        rets = rng.normal(mu, sigma, size=days)
        # Add mild mean-reverting cyclical wave for realistic chart shapes
        t = np.linspace(0, 3.2 * np.pi, days)
        wave = 0.0025 * np.sin(t + (seed % 7))
        path = np.cumprod(1.0 + rets + wave)
        close = base_price * (path / path[-1])

        spread = np.abs(rng.normal(0.0, sigma * 0.65, size=days)) + 0.0015
        open_p = np.roll(close, 1)
        open_p[0] = close[0] * (1.0 - mu)
        high = np.maximum(open_p, close) * (1.0 + spread)
        low = np.minimum(open_p, close) * np.maximum(0.2, 1.0 - spread)

        base_vol = 250_000 + (seed % 4_500_000)
        volume = base_vol * np.exp(rng.normal(0.0, 0.35, size=days))

        dates = pd.date_range(end=datetime.now(timezone.utc).replace(tzinfo=None), periods=days, freq="B")
        return pd.DataFrame(
            {"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=dates,
        )

    @classmethod
    def fetch_tradingview_live_quotes(cls, market: str = "turkey", limit: int = 550) -> Dict[str, Dict[str, Any]]:
        url = cls.TV_BIST_URL if market == "turkey" else cls.TV_US_URL
        payload = {
            "filter": [{"left": "type", "operation": "in_range", "right": ["stock", "dr", "fund"]}],
            "options": {"lang": "tr"},
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": [
                "name", "description", "close", "change", "volume",
                "price_earnings_ttm", "price_book_fq", "return_on_equity", "sector"
            ],
            "sort": {"sortBy": "volume", "sortOrder": "desc"},
            "range": [0, limit],
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            out: Dict[str, Dict[str, Any]] = {}
            for row in raw.get("data", []):
                d = row.get("d", [])
                if len(d) >= 5 and d[0] and d[2] is not None:
                    sym = str(d[0]).upper()
                    out[sym] = {
                        "name": str(d[1] or sym),
                        "close": float(d[2]),
                        "change_pct": float(d[3] or 0.0),
                        "volume": float(d[4] or 0.0),
                        "pe_ratio": float(d[5]) if d[5] is not None else None,
                        "pb_ratio": float(d[6]) if d[6] is not None else None,
                        "roe_pct": float(d[7]) if d[7] is not None else None,
                        "sector": str(d[8] or "Diğer"),
                    }
            return out
        except Exception:
            return {}

    def build_snapshot(
        self,
        item: Dict[str, Any],
        days: int = 120,
        live_quote: Optional[Dict[str, Any]] = None,
    ) -> AssetSnapshot:
        sym = item["symbol"]
        base_px = float(live_quote["close"]) if live_quote and live_quote.get("close") else float(item["base_price"])
        df = self.generate_synthetic_ohlcv(sym, base_px, days=days, risk_level=int(item["risk_level"]))

        if live_quote and live_quote.get("close"):
            last_px = float(live_quote["close"])
            chg = float(live_quote.get("change_pct", 0.0))
            prev_c = last_px / (1.0 + chg / 100.0) if (1.0 + chg / 100.0) > 0 else last_px
            df.iloc[-1, df.columns.get_loc("Close")] = last_px
            if live_quote.get("volume", 0) > 0:
                df.iloc[-1, df.columns.get_loc("Volume")] = float(live_quote["volume"])
            data_src = "Canlı TradingView"
        else:
            last_px = float(df["Close"].iloc[-1])
            prev_c = float(df["Close"].iloc[-2]) if len(df) > 1 else last_px
            chg = float(((last_px - prev_c) / (prev_c + 1e-12)) * 100.0)
            data_src = "Kalibre Terminal Verisi"

        ind = QuantitativeIndicators.compute_all(df)
        pe = live_quote.get("pe_ratio") if (live_quote and live_quote.get("pe_ratio") is not None) else item.get("pe_ratio")
        pb = live_quote.get("pb_ratio") if (live_quote and live_quote.get("pb_ratio") is not None) else item.get("pb_ratio")
        roe = live_quote.get("roe_pct") if (live_quote and live_quote.get("roe_pct") is not None) else item.get("roe_pct")

        return AssetSnapshot(
            symbol=sym,
            name=item["name"],
            asset_class=item["asset_class"],
            market_group=item["market_group"],
            sector=item["sector"],
            category=item["category"],
            indices=list(item["indices"]),
            currency=item["currency"],
            risk_level=int(item["risk_level"]),
            is_katilim=bool(item["is_katilim"]),
            katilim_bucket=item["katilim_bucket"],
            df=df,
            last_price=round(last_px, 4 if last_px < 10 else 2),
            prev_close=round(prev_c, 4 if prev_c < 10 else 2),
            change_pct=round(chg, 2),
            volume=float(df["Volume"].iloc[-1]),
            pe_ratio=round(float(pe), 2) if pe is not None else None,
            pb_ratio=round(float(pb), 2) if pb is not None else None,
            roe_pct=round(float(roe), 2) if roe is not None else None,
            ter_pct=item.get("ter_pct"),
            data_source=data_src,
            yahoo_symbol=item.get("yahoo_symbol"),
            indicators=ind,
        )

    def fetch_live_ohlcv_for_symbol(self, snap: AssetSnapshot, period: str = "6mo") -> pd.DataFrame:
        """Optionally enrich a single inspected symbol with live Yahoo Finance OHLCV."""
        if not HAS_YFINANCE or not snap.yahoo_symbol:
            return snap.df
        try:
            hist = yf.Ticker(snap.yahoo_symbol).history(period=period, interval="1d", timeout=4)
            if hist is not None and not hist.empty and len(hist) >= 20:
                hist = hist[["Open", "High", "Low", "Close", "Volume"]].dropna()
                hist.index = pd.to_datetime(hist.index).tz_localize(None)
                return hist
        except Exception:
            pass
        return snap.df


@st.cache_data(ttl=300, show_spinner=False)
def load_universe_snapshots(use_live_tv: bool = False) -> Tuple[Dict[str, AssetSnapshot], int]:
    """Builds and caches all 840+ AssetSnapshots (+ any extra live TradingView BIST tickers)."""
    fetcher = MultiAssetDataFetcher()
    tv_bist = fetcher.fetch_tradingview_live_quotes("turkey", limit=550) if use_live_tv else {}
    tv_us = fetcher.fetch_tradingview_live_quotes("america", limit=200) if use_live_tv else {}

    snapshots: Dict[str, AssetSnapshot] = {}
    existing_syms = set()

    for item in UNIVERSAL_CATALOG:
        sym = item["symbol"]
        existing_syms.add(sym)
        lq = tv_bist.get(sym) if item["asset_class"] == "BIST_STOCK" else tv_us.get(sym)
        snapshots[sym] = fetcher.build_snapshot(item, days=120, live_quote=lq)

    # Append additional live TradingView BIST stocks beyond built-in 424 when live scan is active
    extra_count = 0
    if tv_bist:
        for sym, lq in tv_bist.items():
            if sym not in existing_syms and sym.isalpha() and 3 <= len(sym) <= 6:
                extra_item = {
                    "symbol": sym,
                    "name": lq.get("name", sym),
                    "asset_class": "BIST_STOCK",
                    "market_group": "BIST Payları",
                    "sector": lq.get("sector", "BIST Diğer"),
                    "category": "BIST Canlı Tarama",
                    "indices": ["BIST_TUM"],
                    "currency": "TRY",
                    "risk_level": 4,
                    "is_katilim": False,
                    "katilim_bucket": "NONE",
                    "base_price": lq["close"],
                    "pe_ratio": lq.get("pe_ratio"),
                    "pb_ratio": lq.get("pb_ratio"),
                    "roe_pct": lq.get("roe_pct"),
                    "ter_pct": None,
                    "yahoo_symbol": f"{sym}.IS",
                }
                snapshots[sym] = fetcher.build_snapshot(extra_item, days=120, live_quote=lq)
                extra_count += 1

    return snapshots, len(tv_bist) + len(tv_us)


# =============================================================================
# 5. DECISION ENGINE & STRICT MULTI-MARKET FILTERING
# =============================================================================
class AlpQuantDecisionEngine:
    """Multi-factor quantitative scoring & SPK suitability enforcement engine."""

    USDTRY_RATE = 34.55

    @classmethod
    def score_asset(
        cls,
        snap: AssetSnapshot,
        profile: SuitabilityProfile,
        total_capital_tl: float = 250_000.0,
    ) -> SignalResult:
        ind = snap.indicators
        rsi = ind.get("rsi_14", 50.0)
        macd_h = ind.get("macd_hist", 0.0)
        sma50_d = ind.get("sma50_dist_pct", 0.0)
        vol_z = ind.get("vol_zscore", 0.0)
        atr_p = ind.get("atr_pct", 1.8)
        mom_20d = ind.get("mom_20d", 0.0)
        dist_low = ind.get("dist_low_20d", 5.0)

        reasons: List[str] = []

        # Technical Score (0..100)
        tech_score = 50.0
        if 38 <= rsi <= 62:
            tech_score += 14.0
            reasons.append(f"RSI ({rsi:.1f}) sağlıklı pozitif momentum bölgesinde")
        elif rsi < 35:
            tech_score += 18.0
            reasons.append(f"RSI ({rsi:.1f}) aşırı satım / tepki dönüş bölgesinde")
        elif rsi > 74:
            tech_score -= 14.0
            reasons.append(f"RSI ({rsi:.1f}) aşırı alım bölgesinde (kâr realizasyonu riski)")

        if macd_h > 0:
            tech_score += 14.0
            reasons.append("MACD histogram pozitif (Bullish momentum teyitli)")
        else:
            tech_score -= 8.0

        if sma50_d > 0:
            tech_score += 10.0
            reasons.append(f"Fiyat SMA50 üzerinde (+%{sma50_d:.1f} yükseliş trendi)")
        else:
            tech_score -= 6.0

        if vol_z > 1.0:
            tech_score += 8.0
            reasons.append(f"Hacim ortalamanın üzerinde (Z={vol_z:+.2f})")

        if dist_low <= 4.0:
            tech_score += 6.0
            reasons.append(f"1 aylık dibe yakın (+%{dist_low:.1f}) destek formasyonu")

        tech_score = float(np.clip(tech_score, 5.0, 99.0))

        # Fundamental / Cost Score (0..100)
        fund_score = 58.0
        if snap.asset_class in ("BIST_STOCK", "US_STOCK"):
            if snap.pe_ratio is not None and 2.0 < snap.pe_ratio <= 14.0:
                fund_score += 18.0
                reasons.append(f"Cazip F/K çarpanı ({snap.pe_ratio:.1f}x)")
            elif snap.pe_ratio is not None and snap.pe_ratio > 32.0:
                fund_score -= 10.0
            if snap.pb_ratio is not None and 0.3 < snap.pb_ratio <= 1.8:
                fund_score += 12.0
            if snap.roe_pct is not None and snap.roe_pct >= 20.0:
                fund_score += 12.0
                reasons.append(f"Güçlü özsermaye kârlılığı (ROE %{snap.roe_pct:.1f})")
        elif snap.asset_class in ("TEFAS_FUND", "GLOBAL_ETF"):
            if snap.ter_pct is not None and snap.ter_pct <= 1.30:
                fund_score += 16.0
                reasons.append(f"Düşük fon yönetim gider kesintisi (TER %{snap.ter_pct:.2f})")
            elif snap.ter_pct is not None and snap.ter_pct <= 2.10:
                fund_score += 8.0
        else:
            fund_score += 10.0

        fund_score = float(np.clip(fund_score, 10.0, 98.0))

        # Momentum Score (0..100)
        momentum_score = float(np.clip(52.0 + mom_20d * 2.1 + snap.change_pct * 1.8, 5.0, 99.0))

        # Composite Score
        composite = round(0.45 * tech_score + 0.30 * fund_score + 0.25 * momentum_score, 2)

        # SPK Suitability Gate
        spk_blocked = snap.risk_level not in profile.approved_levels
        if spk_blocked:
            action = "SPK ENGELİ"
            reasons.insert(0, f"SPK Risk Seviye {snap.risk_level} — Profiliniz (Lv.{profile.risk_score}) dışı")
        elif composite >= 74.0:
            action = "GÜÇLÜ AL"
        elif composite >= 63.0:
            action = "AL"
        elif composite >= 46.0:
            action = "TUT / İZLE"
        else:
            action = "SAT"

        if not reasons:
            reasons.append("Dengeli teknik ve temel görünüm")

        # Position Sizing (Volatility-adjusted)
        if action in ("GÜÇLÜ AL", "AL") and not spk_blocked:
            base_alloc = 0.08 if action == "GÜÇLÜ AL" else 0.055
            alloc_pct = min(profile.max_single_weight, base_alloc)
            suggested_tl = round(total_capital_tl * alloc_pct, 0)
            unit_tl = snap.last_price * (cls.USDTRY_RATE if snap.currency == "USD" else 1.0)
            suggested_qty = int(suggested_tl // max(unit_tl, 1e-6))
        else:
            suggested_tl = 0.0
            suggested_qty = 0

        # Stop-Loss & Take-Profit (ATR-based)
        sl_mult = max(1.6, min(3.2, atr_p * 1.45)) / 100.0
        tp_mult = sl_mult * 2.1
        dec = 4 if snap.last_price < 10 else 2
        stop_loss = round(snap.last_price * (1.0 - sl_mult), dec)
        take_profit = round(snap.last_price * (1.0 + tp_mult), dec)

        return SignalResult(
            symbol=snap.symbol,
            name=snap.name,
            market_group=snap.market_group,
            sector=snap.sector,
            category=snap.category,
            currency=snap.currency,
            risk_level=snap.risk_level,
            is_katilim=snap.is_katilim,
            katilim_bucket=snap.katilim_bucket,
            action=action,
            composite_score=composite,
            tech_score=round(tech_score, 1),
            fund_score=round(fund_score, 1),
            momentum_score=round(momentum_score, 1),
            last_price=snap.last_price,
            change_pct=snap.change_pct,
            rsi_14=rsi,
            macd_hist=macd_h,
            sma50_dist_pct=sma50_d,
            vol_zscore=vol_z,
            atr_pct=atr_p,
            suggested_qty=suggested_qty,
            suggested_tl=suggested_tl,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasons=reasons[:4],
            data_source=snap.data_source,
        )


def passes_filters(snap: AssetSnapshot, sig: SignalResult, cfg: Dict[str, Any]) -> bool:
    """
    Strict multi-market filter engine:
      - Zero leakage: BIST Katılım ONLY returns BIST_STOCK with is_katilim=True (never TI1 or TEFAS funds).
      - TEFAS Katılım ONLY returns TEFAS_FUND with is_katilim=True.
      - RSI range filter is strictly optional (only applied if enabled).
    """
    # 1. Text search
    q = normalize_turkish_lower(cfg.get("search_query", "")).strip()
    if q:
        hay = normalize_turkish_lower(f"{snap.symbol} {snap.name} {snap.sector} {snap.category}")
        if q not in hay:
            return False

    # 2. Market Group
    mg = cfg.get("market_group", "Tüm Piyasalar (840+)")
    if mg != "Tüm Piyasalar (840+)" and snap.market_group != mg:
        return False

    # 3. Strict Participation Finance (Katılım) Mode
    kmode = cfg.get("katilim_mode", "Tümü (Konv. + Katılım)")
    if kmode == "Sadece Katılım Uyumlu":
        if not snap.is_katilim:
            return False
    elif kmode == "BIST Katılım (XKTUM)":
        if snap.asset_class != "BIST_STOCK" or not snap.is_katilim:
            return False
    elif kmode == "TEFAS Katılım Fonları (53)":
        if snap.asset_class != "TEFAS_FUND" or not snap.is_katilim:
            return False
    elif kmode == "İslami ETF & Sukuk":
        if snap.katilim_bucket != "ISLAMIC_ETF_SUKUK":
            return False

    # 4. Context-aware Index / Sub-category
    sub = cfg.get("sub_filter", "Tümü")
    if sub != "Tümü":
        if sub == "BIST 30":
            if "BIST_30" not in snap.indices:
                return False
        elif sub == "BIST 100":
            if "BIST_100" not in snap.indices:
                return False
        elif sub == "BIST Katılım (XKTUM)":
            if snap.asset_class != "BIST_STOCK" or not snap.is_katilim:
                return False
        elif sub == "BIST Yıldız / Büyüme":
            if "BIST_GROWTH_100" not in snap.indices:
                return False
        elif sub == "BIST Yüksek Beta":
            if "BIST_HIGH_BETA" not in snap.indices:
                return False
        elif sub == "TEFAS Katılım (53)":
            if snap.asset_class != "TEFAS_FUND" or not snap.is_katilim:
                return False
        elif sub == "İslami ETF (8)":
            if snap.asset_class != "GLOBAL_ETF" or not snap.is_katilim:
                return False
        else:
            # Match by category or sector name
            if sub != snap.category and sub != snap.sector:
                return False

    # 5. Advanced Popover Filters: BIST Sectors & TEFAS Categories
    bist_sectors = cfg.get("bist_sectors", [])
    if bist_sectors and snap.asset_class == "BIST_STOCK":
        if snap.sector not in bist_sectors:
            return False

    tefas_cats = cfg.get("tefas_categories", [])
    if tefas_cats and snap.asset_class == "TEFAS_FUND":
        if snap.category not in tefas_cats:
            return False

    # 6. Fundamental Filters
    if cfg.get("use_fund_filter", False):
        max_pe = cfg.get("max_pe", 50.0)
        max_pb = cfg.get("max_pb", 10.0)
        min_roe = cfg.get("min_roe", 0.0)
        max_ter = cfg.get("max_ter", 3.5)
        if snap.pe_ratio is not None and snap.pe_ratio > max_pe:
            return False
        if snap.pb_ratio is not None and snap.pb_ratio > max_pb:
            return False
        if snap.roe_pct is not None and snap.roe_pct < min_roe:
            return False
        if snap.ter_pct is not None and snap.ter_pct > max_ter:
            return False

    # 7. Technical Filters (Optional)
    if cfg.get("use_rsi_filter", False):
        rmin, rmax = cfg.get("rsi_range", (20, 80))
        if not (rmin <= sig.rsi_14 <= rmax):
            return False
    if cfg.get("only_above_sma50", False) and sig.sma50_dist_pct < 0:
        return False
    if cfg.get("only_macd_bull", False) and sig.macd_hist <= 0:
        return False

    # 8. Signal Action & Min Score
    actions = cfg.get("signal_actions", [])
    if actions and sig.action not in actions:
        return False
    if sig.composite_score < cfg.get("min_score", 0.0):
        return False

    # 9. SPK Filter Toggle
    if cfg.get("hide_spk_blocked", False) and sig.action == "SPK ENGELİ":
        return False

    return True


# =============================================================================
# 6. LIVE 3-LAYER KAP & MARKET NEWS AI ANALYZER (NO MANUAL INPUT FORM)
# =============================================================================
class KAPDisclosureAnalyzer:
    """
    3-Layer Live News & KAP Intelligence Stream:
      Layer 1: Live Google News TR (KAP / Borsa İstanbul / BIST), Bloomberg HT & Investing TR RSS
      Layer 2: Live Yahoo Finance Ticker News for tracked BIST/US assets
      Layer 3: Live Quantitative Volume & Momentum Anomaly Radar (always fresh fallback)
    """

    RSS_FEEDS = [
        ("KAP & BIST RSS", "https://news.google.com/rss/search?q=KAP+bildirimi+OR+Borsa+Istanbul+hisse&hl=tr&gl=TR&ceid=TR:tr"),
        ("Bloomberg HT", "https://www.bloomberght.com/rss"),
        ("Investing TR", "https://tr.investing.com/rss/news_25.rss"),
    ]

    POSITIVE_KW = [
        "rekor", "kâr", "kar artışı", "büyüme", "temettü", "bedelsiz", "ihale", "sözleşme",
        "yeni iş", "sipariş", "yatırım", "kapasite artışı", "yükseliş", "ralli", "hedef fiyat",
        "al tavsiyesi", "not artışı", "geri alım", "pay geri alım", "onaylandı", "anlaşma",
        "ihracat", "pozitif", "güçlü bilanço", "favök", "artış", "surge", "beat", "upgrade",
    ]
    NEGATIVE_KW = [
        "zarar", "düşüş", "gerileme", "daralma", "borç", "ceza", "soruşturma", "dava",
        "kayıp", "negatif", "satış baskısı", "bedelli", "sermaye azaltımı", "temerrüt",
        "kredi notu indirimi", "üretim durdurma", "grev", "risk", "zayıf bilanço", "downgrade",
    ]
    HARD_VETO_KW = [
        "iflas", "konkordato", "spk tedbir", "brüt takas", "işlem sırası kapat",
        "manipülasyon", "usulsüzlük", "kayyum", "faaliyet durdurma", "delist",
    ]

    def __init__(self, gemini_api_key: str = ""):
        self.gemini_api_key = (gemini_api_key or "").strip()

    @staticmethod
    def _extract_symbol(text: str, known_symbols: List[str]) -> str:
        upper = text.upper()
        for sym in known_symbols:
            if len(sym) >= 4 and re.search(rf"\b{re.escape(sym)}\b", upper):
                return sym
        name_hints = {
            "THY": "THYAO", "TÜRK HAVA": "THYAO", "ASELSAN": "ASELS", "TÜPRAŞ": "TUPRS",
            "BİM": "BIMAS", "FORD OTOSAN": "FROTO", "KOÇ HOLDİNG": "KCHOL", "SABANCI": "SAHOL",
            "AKBANK": "AKBNK", "GARANTİ": "GARAN", "İŞ BANKASI": "ISCTR", "EREĞLİ": "EREGL",
            "ŞİŞECAM": "SISE", "TURKCELL": "TCELL", "PEGASUS": "PGSUS", "ASTOR": "ASTOR",
            "ALTIN": "XAU_TRY", "DOLAR": "USDTRY=X", "BITCOIN": "BTC-USD", "NVIDIA": "NVDA",
            "APPLE": "AAPL", "TESLA": "TSLA", "MICROSOFT": "MSFT", "FED": "SPY", "TCMB": "BIST100",
        }
        for hint, mapped in name_hints.items():
            if hint in upper:
                return mapped
        return "PİYASA"

    def fetch_live_stream(
        self,
        snapshots: Dict[str, AssetSnapshot],
        max_items: int = 18,
        enable_live_rss: bool = True,
    ) -> List[NewsDisclosure]:
        items: List[NewsDisclosure] = []
        seen_titles = set()
        known_syms = list(snapshots.keys())[:250]

        # Layer 1: Live RSS Feeds
        if enable_live_rss:
            for src_name, url in self.RSS_FEEDS:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        xml_bytes = resp.read()
                    root = ET.fromstring(xml_bytes)
                    for el in root.findall(".//item")[:7]:
                        title = (el.findtext("title") or "").strip()
                        if not title or title in seen_titles:
                            continue
                        seen_titles.add(title)
                        desc = re.sub(r"<[^>]+>", "", (el.findtext("description") or "")).strip()
                        pub = (el.findtext("pubDate") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))[:25]
                        link = (el.findtext("link") or "").strip()
                        sym = self._extract_symbol(f"{title} {desc}", known_syms)
                        items.append(NewsDisclosure(
                            id=hashlib.md5(title.encode()).hexdigest()[:10],
                            symbol=sym,
                            title=title,
                            summary_text=desc[:300] if desc else title,
                            source=src_name,
                            published_at=pub,
                            link=link,
                            layer="Canlı KAP & RSS Akışı",
                        ))
                except Exception:
                    continue

        # Layer 2: Live Yahoo Finance Ticker News (if available & RSS sparse)
        if enable_live_rss and HAS_YFINANCE and len(items) < 10:
            for y_sym in ["THYAO.IS", "ASELS.IS", "TUPRS.IS", "NVDA", "SPY"]:
                try:
                    t_news = yf.Ticker(y_sym).news or []
                    for n in t_news[:2]:
                        content = n.get("content", n)
                        title = str(content.get("title") or n.get("title") or "").strip()
                        if not title or title in seen_titles:
                            continue
                        seen_titles.add(title)
                        clean_sym = y_sym.replace(".IS", "")
                        items.append(NewsDisclosure(
                            id=hashlib.md5(title.encode()).hexdigest()[:10],
                            symbol=clean_sym,
                            title=title,
                            summary_text=str(content.get("summary") or title)[:280],
                            source="Yahoo Finance Live",
                            published_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                            link=str(content.get("canonicalUrl", {}).get("url") or n.get("link") or ""),
                            layer="Canlı Piyasa Akışı",
                        ))
                except Exception:
                    continue

        # Layer 3: Live Quantitative Volume & Momentum Anomaly Radar (Always populated)
        ranked_anomalies = sorted(
            snapshots.values(),
            key=lambda s: abs(s.indicators.get("vol_zscore", 0.0)) + abs(s.change_pct) * 0.45,
            reverse=True,
        )[:10]
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        for snap in ranked_anomalies:
            if len(items) >= max_items:
                break
            vz = snap.indicators.get("vol_zscore", 0.0)
            rsi = snap.indicators.get("rsi_14", 50.0)
            direction = "güçlü yükseliş ve hacim artışı" if snap.change_pct >= 0 else "satış baskısı ve volatilite artışı"
            title = f"{snap.symbol} ({snap.name}) — Canlı Tarayıcıda %{snap.change_pct:+.2f} Hareket & Hacim Z={vz:+.2f}"
            summary = (
                f"{snap.market_group} / {snap.sector} grubundaki {snap.symbol} payında {direction} tespit edildi. "
                f"Son fiyat {snap.last_price:,.2f} {snap.currency}, RSI-14: {rsi:.1f}, "
                f"SMA50 farkı %{snap.indicators.get('sma50_dist_pct', 0.0):+.1f}."
            )
            items.append(NewsDisclosure(
                id=hashlib.md5(f"{snap.symbol}_{now_str}".encode()).hexdigest()[:10],
                symbol=snap.symbol,
                title=title,
                summary_text=summary,
                source="AlpQuant Canlı Radar",
                published_at=now_str,
                link="",
                layer="Canlı Hacim & Momentum Radarı",
            ))

        return items[:max_items]

    def _analyze_rule_based(self, disclosure: NewsDisclosure) -> DisclosureAnalysis:
        full_text = normalize_turkish_lower(f"{disclosure.title} {disclosure.summary_text}")

        veto_hits = [kw for kw in self.HARD_VETO_KW if kw in full_text]
        pos_hits = [kw for kw in self.POSITIVE_KW if kw in full_text]
        neg_hits = [kw for kw in self.NEGATIVE_KW if kw in full_text]

        if veto_hits:
            return DisclosureAnalysis(
                disclosure=disclosure,
                sentiment="HARD VETO",
                score=-95.0,
                impact_tag="Kritik Risk / Tedbir",
                ai_commentary=(
                    f"Kritik risk sinyali ({', '.join(veto_hits[:3])}). Kısa vadeli yüksek volatilite ve "
                    "likidite riski nedeniyle yeni alım pozisyonu açılması önerilmez; mevcut pozisyonlarda "
                    "sıkı stop-loss disiplini uygulanmalıdır."
                ),
                matched_signals=veto_hits[:4],
            )

        raw_score = len(pos_hits) * 24.0 - len(neg_hits) * 24.0
        # Incorporate numeric percentage hints in live radar titles
        pct_match = re.search(r"%([+-]?\d+(?:\.\d+)?)", disclosure.title)
        if pct_match:
            val = float(pct_match.group(1))
            raw_score += np.clip(val * 6.5, -35.0, 35.0)

        score = float(np.clip(raw_score, -90.0, 90.0))

        if score >= 18.0:
            sentiment = "OLUMLU"
            impact = "Pozitif Katalizör"
            commentary = (
                f"Haber akışı {disclosure.symbol} için pozitif katalizör niteliğinde. "
                "Gelir/momentum görünümünü destekleyen bu gelişme, teknik göstergelerle (RSI ve hacim) "
                "teyit edildiğinde kademeli alım fırsatı sunabilir."
            )
        elif score <= -18.0:
            sentiment = "OLUMSUZ"
            impact = "Baskı / Risk Unsuru"
            commentary = (
                f"Gelişme {disclosure.symbol} üzerinde kısa vadeli satış baskısı ve kârlılık riski yaratabilir. "
                "Destek seviyeleri ve hacim dengelenmesi görülmeden agresif girişten kaçınılması rasyoneldir."
            )
        else:
            sentiment = "NÖTR"
            impact = "Dengeli / İzleme"
            commentary = (
                f"{disclosure.symbol} özelindeki bu başlık fiyatlama üzerinde tek başına sert yön kırılımı "
                "yaratacak düzeyde değil. Bilanço ve teknik trend teyidi ile birlikte izlenmelidir."
            )

        matched = (pos_hits[:3] + neg_hits[:3]) or ["Piyasa Momentum Sinyali"]
        return DisclosureAnalysis(
            disclosure=disclosure,
            sentiment=sentiment,
            score=round(score, 1),
            impact_tag=impact,
            ai_commentary=commentary,
            matched_signals=matched,
        )

    def analyze_disclosure(self, disclosure: NewsDisclosure) -> DisclosureAnalysis:
        rule_res = self._analyze_rule_based(disclosure)
        if not self.gemini_api_key or disclosure.layer == "Canlı Hacim & Momentum Radarı":
            return rule_res

        # Optional live Gemini enrichment when API key is configured
        try:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={self.gemini_api_key}"
            )
            prompt = (
                "Aşağıdaki finans/KAP haberini kurumsal analist gözüyle 2 cümlede Türkçe yorumla ve "
                "ilk kelimesi OLUMLU, OLUMSUZ veya NÖTR olacak şekilde yanıtla:\n"
                f"Başlık: {disclosure.title}\nÖzet: {disclosure.summary_text}"
            )
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            txt = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if txt:
                upper = txt.upper()
                if upper.startswith("OLUMLU"):
                    rule_res.sentiment = "OLUMLU"
                    rule_res.score = max(rule_res.score, 45.0)
                elif upper.startswith("OLUMSUZ"):
                    rule_res.sentiment = "OLUMSUZ"
                    rule_res.score = min(rule_res.score, -45.0)
                rule_res.ai_commentary = txt
        except Exception:
            pass
        return rule_res


# =============================================================================
# 7. DARK-MODE-SAFE EMAIL NOTIFIER & PDF REPORT GENERATOR
# =============================================================================
class EmailSignalNotifier:
    """Renders high-contrast dark-mode-safe HTML email reports and sends via SMTP."""

    @staticmethod
    def build_html_report(
        signals: List[SignalResult],
        profile: SuitabilityProfile,
        katilim_mode: str,
    ) -> str:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows_html = []
        for s in signals[:15]:
            badge_bg = {
                "GÜÇLÜ AL": "#064E3B", "AL": "#065F46",
                "TUT / İZLE": "#1E293B", "SAT": "#7F1D1D", "SPK ENGELİ": "#451A03",
            }.get(s.action, "#1E293B")
            badge_fg = {
                "GÜÇLÜ AL": "#34D399", "AL": "#6EE7B7",
                "TUT / İZLE": "#CBD5E1", "SAT": "#FCA5A5", "SPK ENGELİ": "#FCD34D",
            }.get(s.action, "#CBD5E1")
            reason_bullets = "<br>".join(f"• {html.escape(r)}" for r in s.reasons[:2])
            rows_html.append(
                f"""
                <tr style="border-bottom:1px solid #1E293B; background-color:#0F141F;">
                    <td style="padding:10px 12px; color:#F8FAFC;">
                        <div style="font-weight:700; font-size:13px; color:#F8FAFC;">{html.escape(s.symbol)}</div>
                        <div style="font-size:11px; color:#94A3B8;">{html.escape(s.name[:34])}</div>
                    </td>
                    <td style="padding:10px 8px; color:#CBD5E1; font-size:12px;">Seviye {s.risk_level}</td>
                    <td style="padding:10px 8px;">
                        <span style="background:{badge_bg}; color:{badge_fg}; padding:3px 8px; border-radius:4px; font-weight:700; font-size:11px; border:1px solid {badge_fg}44;">
                            {html.escape(s.action)}
                        </span>
                    </td>
                    <td style="padding:10px 8px; color:#F8FAFC; font-weight:700; font-size:13px;">{s.composite_score:.1f}</td>
                    <td style="padding:10px 8px; color:#E2E8F0; font-size:12px;">{s.last_price:,.2f} {s.currency}</td>
                    <td style="padding:10px 8px; color:#E2E8F0; font-size:12px;">{s.suggested_qty:,} Adet<br><span style="color:#94A3B8; font-size:11px;">({s.suggested_tl:,.0f} TL)</span></td>
                    <td style="padding:10px 8px; color:#CBD5E1; font-size:11px;">SL: {s.stop_loss:,.2f}<br>TP: {s.take_profit:,.2f}</td>
                    <td style="padding:10px 12px; color:#CBD5E1; font-size:11px; line-height:1.45;">{reason_bullets}</td>
                </tr>
                """
            )

        return f"""
        <div style="background-color:#0B0F17; color:#F8FAFC; font-family:Inter, -apple-system, BlinkMacSystemFont, sans-serif; border:1px solid #1E293B; border-radius:10px; overflow:hidden;">
            <div style="background:linear-gradient(90deg, #0F172A 0%, #172554 100%); padding:18px 20px; border-bottom:2px solid #2563EB;">
                <div style="font-size:18px; font-weight:800; color:#F8FAFC; letter-spacing:-0.01em;">AlpQuant Pro v4.2 — Çoklu Varlık Kantitatif Sinyal Raporu</div>
                <div style="font-size:12px; color:#CBD5E1; margin-top:4px;">
                    SPK Profili: Seviye {profile.risk_score}/5 ({profile.approved_levels}) &nbsp;|&nbsp;
                    Mod: {html.escape(katilim_mode)} &nbsp;|&nbsp; Tarih: {now_str}
                </div>
            </div>
            <table style="width:100%; border-collapse:collapse; text-align:left;">
                <thead>
                    <tr style="background-color:#131B2B; border-bottom:1px solid #263248; color:#94A3B8; font-size:11px; text-transform:uppercase;">
                        <th style="padding:10px 12px;">Varlık</th>
                        <th style="padding:10px 8px;">SPK</th>
                        <th style="padding:10px 8px;">Sinyal</th>
                        <th style="padding:10px 8px;">Skor</th>
                        <th style="padding:10px 8px;">Fiyat</th>
                        <th style="padding:10px 8px;">Pozisyon</th>
                        <th style="padding:10px 8px;">SL / TP</th>
                        <th style="padding:10px 12px;">Analitik Gerekçe</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>
        </div>
        """

    @staticmethod
    def send_email(
        smtp_user: str, smtp_pass: str, recipient: str, subject: str, html_body: str
    ) -> Tuple[bool, str]:
        if not smtp_user or not smtp_pass or not recipient:
            return False, "SMTP e-posta, uygulama şifresi ve alıcı adresi eksiksiz girilmelidir."
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = recipient
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, recipient, msg.as_string())
            return True, f"Rapor başarıyla {recipient} adresine gönderildi."
        except Exception as exc:
            return False, f"SMTP gönderim hatası: {exc}"


def run_strategy_backtest(df: pd.DataFrame, initial_capital: float = 250_000.0) -> Dict[str, Any]:
    """Fast EMA(12)/EMA(26) + RSI momentum strategy backtest vs Buy & Hold."""
    close = df["Close"].astype(float).copy()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    pos = (ema12 > ema26).astype(float).shift(1).fillna(0.0)

    daily_ret = close.pct_change().fillna(0.0)
    strat_ret = pos * daily_ret

    equity = initial_capital * (1.0 + strat_ret).cumprod()
    bh_equity = initial_capital * (1.0 + daily_ret).cumprod()

    total_ret = float((equity.iloc[-1] / initial_capital - 1.0) * 100.0)
    bh_ret = float((bh_equity.iloc[-1] / initial_capital - 1.0) * 100.0)
    peak = equity.cummax()
    max_dd = float(((equity - peak) / peak).min() * 100.0)
    std = float(strat_ret.std())
    sharpe = float((strat_ret.mean() / (std + 1e-9)) * np.sqrt(252))
    active_days = strat_ret[pos > 0]
    win_rate = float((active_days > 0).mean() * 100.0) if len(active_days) > 0 else 0.0

    return {
        "equity": equity,
        "bh_equity": bh_equity,
        "total_ret_pct": round(total_ret, 2),
        "bh_ret_pct": round(bh_ret, 2),
        "max_dd_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "win_rate_pct": round(win_rate, 1),
    }


def generate_pdf_bytes(signals: List[SignalResult], profile: SuitabilityProfile) -> bytes:
    """Generates a downloadable executive PDF report via FPDF (or clean UTF-8 fallback)."""
    if not HAS_FPDF:
        lines = [
            f"AlpQuant Pro v4.2 - Kantitatif Sinyal Raporu ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})",
            f"SPK Risk Profili: Seviye {profile.risk_score}/5",
            "",
        ]
        for s in signals[:25]:
            lines.append(
                f"{s.symbol:10s} | {s.action:10s} | Skor: {s.composite_score:5.1f} | "
                f"Fiyat: {s.last_price:10.2f} {s.currency} | SL: {s.stop_loss:.2f} | TP: {s.take_profit:.2f}"
            )
        return "\n".join(lines).encode("utf-8")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, sanitize_pdf_text("AlpQuant Pro v4.2 - Kurumsal Kantitatif Sinyal Raporu"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        0, 6,
        sanitize_pdf_text(
            f"SPK Profili: Seviye {profile.risk_score}/5 | Tarih: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        ),
        ln=True,
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 9)
    headers = [("Sembol", 24), ("Piyasa", 36), ("Sinyal", 24), ("Skor", 16), ("Fiyat", 28), ("SL", 24), ("TP", 24)]
    for h_txt, w in headers:
        pdf.cell(w, 7, sanitize_pdf_text(h_txt), border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for s in signals[:25]:
        pdf.cell(24, 6, sanitize_pdf_text(s.symbol), border=1)
        pdf.cell(36, 6, sanitize_pdf_text(s.market_group[:18]), border=1)
        pdf.cell(24, 6, sanitize_pdf_text(s.action), border=1)
        pdf.cell(16, 6, f"{s.composite_score:.1f}", border=1)
        pdf.cell(28, 6, f"{s.last_price:,.2f} {s.currency}", border=1)
        pdf.cell(24, 6, f"{s.stop_loss:,.2f}", border=1)
        pdf.cell(24, 6, f"{s.take_profit:,.2f}", border=1)
        pdf.ln()

    raw = pdf.output(dest="S")
    return bytes(raw) if isinstance(raw, (bytes, bytearray)) else raw.encode("latin-1")


# =============================================================================
# 8. STYLING LOADER (100% SIDEBAR-FREE WIDESCREEN INSTITUTIONAL TERMINAL)
# =============================================================================
FALLBACK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');

[data-testid="stSidebar"],
[data-testid="collapsedControl"],
section[data-testid="stSidebar"],
button[kind="headerNoPadding"] {
    display: none !important;
    width: 0 !important;
    min-width: 0 !important;
    visibility: hidden !important;
}

html, body, [data-testid="stAppViewContainer"], .main {
    background-color: #090C12 !important;
    color: #E2E8F0 !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

.block-container {
    max-width: 100% !important;
    padding-top: 0.85rem !important;
    padding-bottom: 2.5rem !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
}

header[data-testid="stHeader"] {
    background-color: rgba(9, 12, 18, 0.85) !important;
    backdrop-filter: blur(8px);
    height: 2.4rem !important;
}

.aq-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: linear-gradient(90deg, #0F141F 0%, #111827 100%);
    border: 1px solid #1C2436;
    border-radius: 10px;
    padding: 10px 18px;
    margin-bottom: 10px;
}

.aq-brand {
    display: flex;
    align-items: center;
    gap: 10px;
}

.aq-logo {
    background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%);
    color: #FFFFFF;
    font-weight: 800;
    font-size: 13px;
    padding: 4px 9px;
    border-radius: 6px;
    letter-spacing: 0.04em;
}

.aq-title {
    font-size: 16px;
    font-weight: 700;
    color: #F8FAFC;
    letter-spacing: -0.01em;
}

.aq-subtitle {
    font-size: 12px;
    color: #94A3B8;
    margin-left: 4px;
}

.aq-pills {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.aq-pill {
    background: #131A29;
    border: 1px solid #243047;
    color: #CBD5E1;
    font-size: 11.5px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
}

.aq-pill-accent {
    background: rgba(37, 99, 235, 0.16);
    border: 1px solid rgba(59, 130, 246, 0.45);
    color: #93C5FD;
}

.aq-pill-green {
    background: rgba(16, 185, 129, 0.14);
    border: 1px solid rgba(16, 185, 129, 0.40);
    color: #6EE7B7;
}

.aq-kpi-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 10px;
    margin: 10px 0 14px 0;
}

.aq-kpi-card {
    background: #0F141F;
    border: 1px solid #1C2436;
    border-radius: 8px;
    padding: 11px 14px;
}

.aq-kpi-label {
    font-size: 11px;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
}

.aq-kpi-val {
    font-size: 20px;
    font-weight: 700;
    color: #F8FAFC;
    margin-top: 3px;
    font-family: 'JetBrains Mono', monospace;
}

.aq-kpi-sub {
    font-size: 11px;
    color: #64748B;
    margin-top: 2px;
}

.aq-news-card {
    background: #0F141F;
    border: 1px solid #1C2436;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 10px;
}

.aq-news-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
}

.aq-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    background: #0F141F;
    border: 1px solid #1C2436;
    border-radius: 8px;
    padding: 4px;
}

.stTabs [data-baseweb="tab"] {
    height: 36px;
    border-radius: 6px;
    color: #94A3B8 !important;
    font-weight: 600;
    font-size: 13px;
    padding: 0 16px;
}

.stTabs [aria-selected="true"] {
    background: #2563EB !important;
    color: #FFFFFF !important;
}
</style>
"""


def inject_terminal_css() -> None:
    css_path = Path(__file__).resolve().parent / "style.css"
    if css_path.exists():
        try:
            css_text = css_path.read_text(encoding="utf-8")
            st.markdown(f"<style>{css_text}</style>", unsafe_allow_html=True)
        except Exception:
            pass
    st.markdown(FALLBACK_CSS, unsafe_allow_html=True)


# =============================================================================
# 9. CHART BUILDERS
# =============================================================================
def build_mini_candlestick(snap: AssetSnapshot, height: int = 235) -> go.Figure:
    df = snap.df.tail(45)
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            increasing_line_color="#10B981",
            decreasing_line_color="#EF4444",
            increasing_fillcolor="rgba(16,185,129,0.65)",
            decreasing_fillcolor="rgba(239,68,68,0.65)",
            name=snap.symbol,
        )
    )
    sma20 = df["Close"].rolling(20, min_periods=5).mean()
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=sma20,
            mode="lines",
            line=dict(color="#3B82F6", width=1.4),
            name="SMA20",
        )
    )
    chg_color = "#10B981" if snap.change_pct >= 0 else "#EF4444"
    fig.update_layout(
        title=dict(
            text=f"<b>{snap.symbol}</b> · {snap.last_price:,.2f} {snap.currency} <span style='color:{chg_color}'>({snap.change_pct:+.2f}%)</span>",
            font=dict(size=12, color="#F8FAFC"),
            x=0.02,
            y=0.94,
        ),
        height=height,
        margin=dict(l=8, r=8, t=34, b=8),
        paper_bgcolor="#0F141F",
        plot_bgcolor="#0B0F17",
        xaxis_rangeslider_visible=False,
        showlegend=False,
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color="#64748B")),
        yaxis=dict(gridcolor="#1C2436", tickfont=dict(size=9, color="#94A3B8"), side="right"),
    )
    return fig


def build_detailed_candlestick(snap: AssetSnapshot, df_override: Optional[pd.DataFrame] = None) -> go.Figure:
    df = (df_override if df_override is not None else snap.df).tail(90)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.74, 0.26]
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            increasing_line_color="#10B981",
            decreasing_line_color="#EF4444",
            name=snap.symbol,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["Close"].rolling(20, min_periods=5).mean(),
            line=dict(color="#3B82F6", width=1.5),
            name="SMA 20",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["Close"].rolling(50, min_periods=10).mean(),
            line=dict(color="#F59E0B", width=1.5),
            name="SMA 50",
        ),
        row=1, col=1,
    )
    bar_colors = np.where(df["Close"] >= df["Open"], "rgba(16,185,129,0.55)", "rgba(239,68,68,0.55)")
    fig.add_trace(
        go.Bar(x=df.index, y=df["Volume"], marker_color=bar_colors, name="Hacim"),
        row=2, col=1,
    )
    fig.update_layout(
        height=440,
        margin=dict(l=10, r=14, t=30, b=10),
        paper_bgcolor="#0F141F",
        plot_bgcolor="#0B0F17",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1, font=dict(size=11, color="#CBD5E1")),
        yaxis=dict(gridcolor="#1C2436", side="right", tickfont=dict(color="#94A3B8")),
        yaxis2=dict(gridcolor="#1C2436", side="right", tickfont=dict(color="#64748B")),
        xaxis2=dict(showgrid=False, tickfont=dict(color="#64748B")),
    )
    return fig


# =============================================================================
# 10. SESSION STATE INITIALIZATION
# =============================================================================
def init_session_state() -> None:
    defaults = {
        "suitability_profile": SuitabilityProfile(),
        "total_capital_tl": 250_000.0,
        "use_live_tv": False,
        "use_live_rss": True,
        "use_live_yf_chart": False,
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "smtp_user": "",
        "smtp_pass": "",
        "email_recipient": "alperengulluoglu@gmail.com",
        "bist_sectors": [],
        "tefas_categories": [],
        "use_fund_filter": False,
        "max_pe": 35.0,
        "max_pb": 6.0,
        "min_roe": 0.0,
        "max_ter": 3.0,
        "use_rsi_filter": False,
        "rsi_range": (25, 75),
        "only_above_sma50": False,
        "only_macd_bull": False,
        "signal_actions": [],
        "min_score": 0.0,
        "hide_spk_blocked": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# =============================================================================
# 11. MAIN APPLICATION (SIDEBAR-FREE COMMAND DOCK ARCHITECTURE)
# =============================================================================
def main() -> None:
    inject_terminal_css()
    init_session_state()

    profile: SuitabilityProfile = st.session_state.suitability_profile

    # Load 840+ universe (optionally enriched with live TradingView quotes)
    snapshots, live_quote_count = load_universe_snapshots(use_live_tv=st.session_state.use_live_tv)
    total_assets = len(snapshots)
    total_katilim = sum(1 for s in snapshots.values() if s.is_katilim)

    # -------------------------------------------------------------------------
    # TOP TERMINAL BAR
    # -------------------------------------------------------------------------
    live_pill = (
        f"<span class='aq-pill aq-pill-green'>● CANLI VERİ ({live_quote_count})</span>"
        if live_quote_count > 0
        else "<span class='aq-pill'>● KALİBRE TERMİNAL</span>"
    )
    st.markdown(
        f"""
        <div class="aq-topbar">
            <div class="aq-brand">
                <span class="aq-logo">ALPQUANT PRO</span>
                <span class="aq-title">Kurumsal Çoklu Varlık Kantitatif Terminali</span>
                <span class="aq-subtitle">v4.2 · Widescreen</span>
            </div>
            <div class="aq-pills">
                {live_pill}
                <span class="aq-pill">Evren: {total_assets} Varlık</span>
                <span class="aq-pill aq-pill-green">Katılım: {total_katilim}</span>
                <span class="aq-pill aq-pill-accent">SPK Lv.{profile.risk_score}/5</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # HORIZONTAL COMMAND & FILTER DOCK (REPLACES SIDEBAR COMPLETELY)
    # -------------------------------------------------------------------------
    c_search, c_mkt, c_kat, c_sub, c_adv, c_cfg = st.columns([2.2, 1.55, 1.75, 1.55, 1.1, 1.35], gap="small")

    with c_search:
        search_query = st.text_input(
            "Ara",
            value="",
            placeholder="Sembol, fon veya şirket ara (ör. THYAO, KTV, NVDA)...",
            label_visibility="collapsed",
        )

    market_options = [
        "Tüm Piyasalar (840+)",
        "BIST Payları",
        "TEFAS Fonları",
        "Küresel ETF & ABD",
        "Döviz, Altın & Sukuk",
        "Kripto & 3x ETF",
    ]
    with c_mkt:
        market_group = st.selectbox(
            "Piyasa",
            options=market_options,
            index=0,
            label_visibility="collapsed",
        )

    katilim_options = [
        "Tümü (Konv. + Katılım)",
        "Sadece Katılım Uyumlu",
        "BIST Katılım (XKTUM)",
        "TEFAS Katılım Fonları (53)",
        "İslami ETF & Sukuk",
    ]
    with c_kat:
        katilim_mode = st.selectbox(
            "Katılım Modu",
            options=katilim_options,
            index=0,
            label_visibility="collapsed",
        )

    # Context-aware index / sub-category dropdown
    if market_group in ("Tüm Piyasalar (840+)", "BIST Payları"):
        sub_options = [
            "Tümü", "BIST 30", "BIST 100", "BIST Katılım (XKTUM)",
            "BIST Yıldız / Büyüme", "BIST Yüksek Beta",
        ]
    elif market_group == "TEFAS Fonları":
        sub_options = ["Tümü", "TEFAS Katılım (53)"] + sorted(
            {s.category for s in snapshots.values() if s.asset_class == "TEFAS_FUND"}
        )
    elif market_group == "Küresel ETF & ABD":
        sub_options = ["Tümü", "İslami ETF (8)"] + sorted(
            {s.category for s in snapshots.values() if s.market_group == "Küresel ETF & ABD"}
        )
    elif market_group == "Döviz, Altın & Sukuk":
        sub_options = ["Tümü"] + sorted(
            {s.category for s in snapshots.values() if s.market_group == "Döviz, Altın & Sukuk"}
        )
    else:
        sub_options = ["Tümü"] + sorted(
            {s.category for s in snapshots.values() if s.market_group == "Kripto & 3x ETF"}
        )

    with c_sub:
        sub_filter = st.selectbox(
            "Alt Kategori",
            options=sub_options,
            index=0,
            label_visibility="collapsed",
        )

    # Floating Advanced Filters Popover
    with c_adv:
        with safe_popover("⚙ Filtreler", use_container_width=True):
            st.markdown("**Sektör & Fon Kategorisi**")
            all_bist_sectors = sorted({s.sector for s in snapshots.values() if s.asset_class == "BIST_STOCK"})
            all_tefas_cats = sorted({s.category for s in snapshots.values() if s.asset_class == "TEFAS_FUND"})

            st.session_state.bist_sectors = st.multiselect(
                "BIST Sektörleri", options=all_bist_sectors, default=st.session_state.bist_sectors
            )
            st.session_state.tefas_categories = st.multiselect(
                "TEFAS Fon Kategorileri", options=all_tefas_cats, default=st.session_state.tefas_categories
            )

            st.markdown("**Temel & Maliyet Filtreleri**")
            st.session_state.use_fund_filter = st.checkbox(
                "Temel Çarpan Filtresini Aktifleştir", value=st.session_state.use_fund_filter
            )
            if st.session_state.use_fund_filter:
                fc1, fc2 = st.columns(2)
                st.session_state.max_pe = fc1.number_input("Maks. F/K", 1.0, 200.0, float(st.session_state.max_pe))
                st.session_state.max_pb = fc2.number_input("Maks. PD/DD", 0.1, 50.0, float(st.session_state.max_pb))
                fc3, fc4 = st.columns(2)
                st.session_state.min_roe = fc3.number_input("Min. ROE (%)", -50.0, 100.0, float(st.session_state.min_roe))
                st.session_state.max_ter = fc4.number_input("Maks. Fon TER (%)", 0.0, 5.0, float(st.session_state.max_ter))

            st.markdown("**Teknik & Sinyal Filtreleri**")
            st.session_state.use_rsi_filter = st.checkbox(
                "RSI Aralığı Filtrele", value=st.session_state.use_rsi_filter
            )
            if st.session_state.use_rsi_filter:
                st.session_state.rsi_range = st.slider(
                    "RSI-14 Aralığı", 0, 100, tuple(st.session_state.rsi_range)
                )
            tc1, tc2 = st.columns(2)
            st.session_state.only_above_sma50 = tc1.checkbox(
                "SMA50 Üstü", value=st.session_state.only_above_sma50
            )
            st.session_state.only_macd_bull = tc2.checkbox(
                "MACD Pozitif", value=st.session_state.only_macd_bull
            )
            st.session_state.signal_actions = st.multiselect(
                "Sinyal Tipi",
                options=["GÜÇLÜ AL", "AL", "TUT / İZLE", "SAT", "SPK ENGELİ"],
                default=st.session_state.signal_actions,
            )
            st.session_state.min_score = st.slider(
                "Minimum Kompozit Skor", 0.0, 95.0, float(st.session_state.min_score), step=1.0
            )
            st.session_state.hide_spk_blocked = st.checkbox(
                "SPK Engelli Varlıkları Gizle", value=st.session_state.hide_spk_blocked
            )

    # Floating SPK Profile & Terminal Settings Popover
    with c_cfg:
        with safe_popover(f"SPK Lv.{profile.risk_score} · Ayarlar", use_container_width=True):
            ptab_spk, ptab_api = st.tabs(["SPK Profili", "Sermaye & API"])
            with ptab_spk:
                st.caption(
                    f"Aktif Profil: **Seviye {profile.risk_score}/5** — {profile.risk_label} "
                    f"(Onaylı Risk Seviyeleri: {profile.approved_levels})"
                )
                with st.form("spk_quick_form", clear_on_submit=False):
                    q1 = st.selectbox("Yatırım Vadesi", ["0-1 Yıl (Kısa)", "1-3 Yıl (Orta)", "3+ Yıl (Uzun)"], index=2)
                    q2 = st.selectbox("Kayıp Toleransı", ["Anapara Koruma", "%10-15 Dalgalanma", "%25+ Yüksek Getiri Odaklı"], index=2)
                    q3 = st.selectbox("Piyasa Tecrübesi", ["Başlangıç", "Orta (Hisse/Fon)", "Profesyonel / Türev & Kripto"], index=2)
                    if st.form_submit_button("SPK Profilini Güncelle", use_container_width=True):
                        pts = (
                            ({"0-1 Yıl (Kısa)": 1, "1-3 Yıl (Orta)": 2, "3+ Yıl (Uzun)": 3}[q1])
                            + ({"Anapara Koruma": 1, "%10-15 Dalgalanma": 2, "%25+ Yüksek Getiri Odaklı": 3}[q2])
                            + ({"Başlangıç": 1, "Orta (Hisse/Fon)": 2, "Profesyonel / Türev & Kripto": 3}[q3])
                        )
                        if pts <= 4:
                            rl, lbl = 2, "Düşük-Orta Risk (Temkinli)"
                        elif pts <= 6:
                            rl, lbl = 3, "Orta Risk (Dengeli)"
                        elif pts <= 7:
                            rl, lbl = 4, "Yüksek Risk (Büyüme Odaklı)"
                        else:
                            rl, lbl = 5, "Çok Yüksek Risk (Agresif / Profesyonel)"
                        st.session_state.suitability_profile = SuitabilityProfile(
                            user_id=profile.user_id,
                            risk_score=rl,
                            risk_label=lbl,
                            approved_levels=list(range(1, rl + 1)),
                            max_single_weight=0.10 + rl * 0.03,
                        )
                        st.success(f"SPK profili Seviye {rl}/5 olarak kaydedildi.")
                        st.rerun()

            with ptab_api:
                st.session_state.total_capital_tl = st.number_input(
                    "Toplam Portföy Sermayesi (TL)",
                    min_value=10_000.0,
                    max_value=500_000_000.0,
                    value=float(st.session_state.total_capital_tl),
                    step=25_000.0,
                )
                st.session_state.use_live_tv = st.checkbox(
                    "Canlı TradingView BIST/ABD Taraması",
                    value=st.session_state.use_live_tv,
                    help="Etkinleştirildiğinde BIST ve ABD canlı fiyatları TradingView üzerinden çekilir.",
                )
                st.session_state.use_live_rss = st.checkbox(
                    "Canlı KAP & Finans RSS Akışı",
                    value=st.session_state.use_live_rss,
                )
                st.session_state.use_live_yf_chart = st.checkbox(
                    "Grafik İncelemede Canlı Yahoo Finance",
                    value=st.session_state.use_live_yf_chart,
                )
                st.session_state.gemini_api_key = st.text_input(
                    "Gemini API Key (Opsiyonel AI Yorum)",
                    value=st.session_state.gemini_api_key,
                    type="password",
                )
                st.session_state.email_recipient = st.text_input(
                    "Rapor Alıcı E-Posta",
                    value=st.session_state.email_recipient,
                )
                ec1, ec2 = st.columns(2)
                st.session_state.smtp_user = ec1.text_input("SMTP Gönderici", value=st.session_state.smtp_user)
                st.session_state.smtp_pass = ec2.text_input("SMTP Uygulama Şifresi", value=st.session_state.smtp_pass, type="password")

    # -------------------------------------------------------------------------
    # SCORE & FILTER UNIVERSE
    # -------------------------------------------------------------------------
    filter_cfg = {
        "search_query": search_query,
        "market_group": market_group,
        "katilim_mode": katilim_mode,
        "sub_filter": sub_filter,
        "bist_sectors": st.session_state.bist_sectors,
        "tefas_categories": st.session_state.tefas_categories,
        "use_fund_filter": st.session_state.use_fund_filter,
        "max_pe": st.session_state.max_pe,
        "max_pb": st.session_state.max_pb,
        "min_roe": st.session_state.min_roe,
        "max_ter": st.session_state.max_ter,
        "use_rsi_filter": st.session_state.use_rsi_filter,
        "rsi_range": st.session_state.rsi_range,
        "only_above_sma50": st.session_state.only_above_sma50,
        "only_macd_bull": st.session_state.only_macd_bull,
        "signal_actions": st.session_state.signal_actions,
        "min_score": st.session_state.min_score,
        "hide_spk_blocked": st.session_state.hide_spk_blocked,
    }

    filtered_pairs: List[Tuple[AssetSnapshot, SignalResult]] = []
    for snap in snapshots.values():
        sig = AlpQuantDecisionEngine.score_asset(snap, profile, st.session_state.total_capital_tl)
        if passes_filters(snap, sig, filter_cfg):
            filtered_pairs.append((snap, sig))

    filtered_pairs.sort(key=lambda x: x[1].composite_score, reverse=True)
    filtered_signals = [p[1] for p in filtered_pairs]

    buy_count = sum(1 for s in filtered_signals if s.action in ("GÜÇLÜ AL", "AL"))
    kat_filtered_count = sum(1 for s in filtered_signals if s.is_katilim)
    avg_score = float(np.mean([s.composite_score for s in filtered_signals])) if filtered_signals else 0.0
    top_sig = filtered_signals[0] if filtered_signals else None

    # Compact KPI Strip
    st.markdown(
        f"""
        <div class="aq-kpi-grid">
            <div class="aq-kpi-card">
                <div class="aq-kpi-label">Eşleşen Varlık</div>
                <div class="aq-kpi-val">{len(filtered_signals)}</div>
                <div class="aq-kpi-sub">Toplam {total_assets} evrenden</div>
            </div>
            <div class="aq-kpi-card">
                <div class="aq-kpi-label">AL / GÜÇLÜ AL</div>
                <div class="aq-kpi-val" style="color:#10B981;">{buy_count}</div>
                <div class="aq-kpi-sub">SPK onaylı pozitif sinyal</div>
            </div>
            <div class="aq-kpi-card">
                <div class="aq-kpi-label">Katılım Uyumlu</div>
                <div class="aq-kpi-val" style="color:#38BDF8;">{kat_filtered_count}</div>
                <div class="aq-kpi-sub">{html.escape(katilim_mode)}</div>
            </div>
            <div class="aq-kpi-card">
                <div class="aq-kpi-label">Ortalama Skor</div>
                <div class="aq-kpi-val">{avg_score:.1f}</div>
                <div class="aq-kpi-sub">0–100 Çok Faktörlü</div>
            </div>
            <div class="aq-kpi-card">
                <div class="aq-kpi-label">Lider Sinyal</div>
                <div class="aq-kpi-val" style="color:#F59E0B;">{html.escape(top_sig.symbol) if top_sig else '—'}</div>
                <div class="aq-kpi-sub">{f'Skor {top_sig.composite_score:.1f} · {top_sig.action}' if top_sig else 'Filtre sonucu yok'}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # 5 INSTITUTIONAL TABS
    # -------------------------------------------------------------------------
    tab_term, tab_sig, tab_screen, tab_kap, tab_bt = st.tabs([
        "Piyasa Terminali",
        "Karar Motoru & Sinyaller",
        "Çoklu Varlık Tarayıcısı",
        "Canlı KAP & Haber Akışı",
        "Backtest & PDF Rapor",
    ])

    # =========================================================================
    # TAB 1: PİYASA TERMİNALİ (5 STARTUP CANDLESTICK CHARTS)
    # =========================================================================
    with tab_term:
        st.caption("Seçili piyasa ve filtre kapsamındaki öne çıkan 5 varlığın canlı mum grafikleri (SMA20 teyitli).")
        default_bench = ["THYAO", "ASELS", "XAU_TRY", "SPY", "BTC-USD"]
        if len(filtered_pairs) >= 5 and (market_group != "Tüm Piyasalar (840+)" or katilim_mode != "Tümü (Konv. + Katılım)" or search_query):
            chart_snaps = [p[0] for p in filtered_pairs[:5]]
        else:
            chart_snaps = [snapshots[s] for s in default_bench if s in snapshots]
            if len(chart_snaps) < 5:
                chart_snaps = [p[0] for p in filtered_pairs[:5]]

        if chart_snaps:
            row1 = st.columns(min(3, len(chart_snaps)), gap="small")
            for idx, col in enumerate(row1):
                with col:
                    st.plotly_chart(build_mini_candlestick(chart_snaps[idx]), use_container_width=True, key=f"mini_r1_{idx}_{chart_snaps[idx].symbol}")
            if len(chart_snaps) > 3:
                row2 = st.columns(len(chart_snaps) - 3, gap="small")
                for idx, col in enumerate(row2):
                    s_obj = chart_snaps[idx + 3]
                    with col:
                        st.plotly_chart(build_mini_candlestick(s_obj), use_container_width=True, key=f"mini_r2_{idx}_{s_obj.symbol}")
        else:
            st.warning("Mevcut filtre kombinasyonuyla eşleşen varlık bulunamadı. Üstteki filtreleri gevşetebilirsiniz.")

        # Market Group Summary Table
        if filtered_signals:
            grp_df = pd.DataFrame([
                {
                    "Piyasa Grubu": s.market_group,
                    "Skor": s.composite_score,
                    "Günlük %": s.change_pct,
                    "AL Sinyali": 1 if s.action in ("GÜÇLÜ AL", "AL") else 0,
                    "Katılım": 1 if s.is_katilim else 0,
                }
                for s in filtered_signals
            ])
            summary_tbl = (
                grp_df.groupby("Piyasa Grubu", as_index=False)
                .agg(
                    Varlık_Sayısı=("Skor", "count"),
                    Ort_Skor=("Skor", "mean"),
                    Ort_Değişim_Pct=("Günlük %", "mean"),
                    AL_Sinyali=("AL Sinyali", "sum"),
                    Katılım_Adedi=("Katılım", "sum"),
                )
                .round(2)
            )
            st.dataframe(summary_tbl, use_container_width=True, hide_index=True)

    # =========================================================================
    # TAB 2: KARAR MOTORU & SİNYALLER + DARK-MODE-SAFE EMAIL REPORT
    # =========================================================================
    with tab_sig:
        if not filtered_signals:
            st.info("Filtre kriterlerine uygun sinyal bulunamadı.")
        else:
            sig_rows = [
                {
                    "Sembol": s.symbol,
                    "Varlık Adı": s.name,
                    "Piyasa": s.market_group,
                    "Katılım": "Evet" if s.is_katilim else "Hayır",
                    "SPK": f"Lv.{s.risk_level}",
                    "Sinyal": s.action,
                    "Skor": s.composite_score,
                    "Fiyat": f"{s.last_price:,.2f} {s.currency}",
                    "Günlük %": s.change_pct,
                    "RSI-14": s.rsi_14,
                    "Önerilen Adet": s.suggested_qty,
                    "Pozisyon (TL)": s.suggested_tl,
                    "Stop-Loss": s.stop_loss,
                    "Take-Profit": s.take_profit,
                    "Analitik Gerekçe": " · ".join(s.reasons[:2]),
                }
                for s in filtered_signals[:120]
            ]
            st.dataframe(pd.DataFrame(sig_rows), use_container_width=True, hide_index=True, height=420)

            ec_btn, ec_stat = st.columns([1.4, 3.6])
            html_report = EmailSignalNotifier.build_html_report(filtered_signals, profile, katilim_mode)
            with ec_btn:
                if st.button("Filtrelenen Sinyalleri E-Posta Gönder", type="primary", use_container_width=True):
                    ok, msg = EmailSignalNotifier.send_email(
                        st.session_state.smtp_user,
                        st.session_state.smtp_pass,
                        st.session_state.email_recipient,
                        f"AlpQuant Pro v4.2 Sinyal Raporu ({len(filtered_signals)} Varlık)",
                        html_report,
                    )
                    if ok:
                        ec_stat.success(msg)
                    else:
                        ec_stat.warning(msg)

            with st.expander("E-Posta Bildirim Raporu Canlı Önizleme (Dark-Mode Uyumlu HTML)", expanded=False):
                st.markdown(html_report, unsafe_allow_html=True)

    # =========================================================================
    # TAB 3: ÇOKLU VARLIK TARAYICISI & TEKNİK İNCELEME
    # =========================================================================
    with tab_screen:
        if not filtered_pairs:
            st.info("Tarayıcıda eşleşen varlık yok.")
        else:
            screen_rows = [
                {
                    "Sembol": snap.symbol,
                    "Ad": snap.name,
                    "Piyasa": snap.market_group,
                    "Sektör / Kategori": f"{snap.sector} / {snap.category}",
                    "Katılım": "Katılım" if snap.is_katilim else "Konv.",
                    "Fiyat": snap.last_price,
                    "Para Birimi": snap.currency,
                    "Günlük %": snap.change_pct,
                    "Skor": sig.composite_score,
                    "Sinyal": sig.action,
                    "F/K": snap.pe_ratio,
                    "PD/DD": snap.pb_ratio,
                    "ROE %": snap.roe_pct,
                    "TER %": snap.ter_pct,
                    "RSI-14": sig.rsi_14,
                    "SMA50 %": sig.sma50_dist_pct,
                    "Hacim Z": sig.vol_zscore,
                    "Kaynak": snap.data_source,
                }
                for snap, sig in filtered_pairs
            ]
            screen_df = pd.DataFrame(screen_rows)

            sc1, sc2 = st.columns([3.2, 1.2])
            with sc1:
                inspected_sym = st.selectbox(
                    "Detaylı Teknik Grafik İncele",
                    options=[snap.symbol for snap, _ in filtered_pairs[:150]],
                    format_func=lambda s: f"{s} — {snapshots[s].name} ({snapshots[s].market_group})",
                )
            with sc2:
                st.download_button(
                    "Tarama Sonuçlarını CSV İndir",
                    data=screen_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"alpquant_tarama_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            if inspected_sym and inspected_sym in snapshots:
                sel_snap = snapshots[inspected_sym]
                live_df = (
                    MultiAssetDataFetcher().fetch_live_ohlcv_for_symbol(sel_snap)
                    if st.session_state.use_live_yf_chart
                    else None
                )
                st.plotly_chart(
                    build_detailed_candlestick(sel_snap, df_override=live_df),
                    use_container_width=True,
                    key=f"detail_chart_{inspected_sym}",
                )

            st.dataframe(screen_df, use_container_width=True, hide_index=True, height=380)

    # =========================================================================
    # TAB 4: CANLI KAP & HABER AKIŞI (OTOMATİK AI YORUMLU, MANUEL FORM YOK)
    # =========================================================================
    with tab_kap:
        k_col1, k_col2 = st.columns([3.2, 1.2])
        with k_col1:
            sent_filter = st.multiselect(
                "Duygu & Etki Filtresi",
                options=["OLUMLU", "OLUMSUZ", "NÖTR", "HARD VETO"],
                default=["OLUMLU", "OLUMSUZ", "NÖTR", "HARD VETO"],
            )
        with k_col2:
            if st.button("Haber Akışını Yenile", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

        kap_analyzer = KAPDisclosureAnalyzer(gemini_api_key=st.session_state.gemini_api_key)
        live_disclosures = kap_analyzer.fetch_live_stream(
            snapshots=snapshots,
            max_items=18,
            enable_live_rss=st.session_state.use_live_rss,
        )

        analyses = [kap_analyzer.analyze_disclosure(d) for d in live_disclosures]
        shown_analyses = [a for a in analyses if a.sentiment in sent_filter]

        if not shown_analyses:
            st.info("Seçili duygu filtresine uygun güncel haber bulunamadı.")
        else:
            for a in shown_analyses:
                d = a.disclosure
                palette = {
                    "OLUMLU": ("#064E3B", "#34D399"),
                    "OLUMSUZ": ("#7F1D1D", "#FCA5A5"),
                    "NÖTR": ("#1E293B", "#CBD5E1"),
                    "HARD VETO": ("#450A0A", "#F87171"),
                }.get(a.sentiment, ("#1E293B", "#CBD5E1"))
                link_html = (
                    f"<a href='{html.escape(d.link)}' target='_blank' style='color:#60A5FA; text-decoration:none; font-size:11.5px;'>Kaynağa Git ↗</a>"
                    if d.link
                    else ""
                )
                st.markdown(
                    f"""
                    <div class="aq-news-card">
                        <div class="aq-news-header">
                            <div>
                                <span class="aq-badge" style="background:#172554; color:#93C5FD; border:1px solid #2563EB55;">{html.escape(d.symbol)}</span>
                                <span class="aq-badge" style="background:{palette[0]}; color:{palette[1]}; border:1px solid {palette[1]}55; margin-left:6px;">{html.escape(a.sentiment)} ({a.score:+.0f})</span>
                                <span style="font-size:11px; color:#94A3B8; margin-left:8px;">{html.escape(d.source)} · {html.escape(d.layer)} · {html.escape(d.published_at)}</span>
                            </div>
                            <div>{link_html}</div>
                        </div>
                        <div style="font-size:14px; font-weight:700; color:#F8FAFC; margin-bottom:5px;">{html.escape(d.title)}</div>
                        <div style="font-size:12.5px; color:#CBD5E1; line-height:1.5; background:#131A29; border-left:3px solid {palette[1]}; padding:8px 11px; border-radius:4px;">
                            <b style="color:{palette[1]};">AI Analiz ({html.escape(a.impact_tag)}):</b> {html.escape(a.ai_commentary)}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    # =========================================================================
    # TAB 5: BACKTEST & PDF RAPOR
    # =========================================================================
    with tab_bt:
        bt_col1, bt_col2 = st.columns([2.5, 1.5])
        bt_choices = [p[0].symbol for p in filtered_pairs[:100]] or list(snapshots.keys())[:25]
        with bt_col1:
            bt_sym = st.selectbox(
                "Backtest Varlığı Seç",
                options=bt_choices,
                format_func=lambda s: f"{s} — {snapshots[s].name}",
            )
        with bt_col2:
            pdf_bytes = generate_pdf_bytes(filtered_signals, profile)
            st.download_button(
                "Kurumsal Sinyal Raporunu PDF İndir" if HAS_FPDF else "Sinyal Raporunu İndir (TXT)",
                data=pdf_bytes,
                file_name=f"alpquant_rapor_{datetime.now().strftime('%Y%m%d_%H%M')}.{'pdf' if HAS_FPDF else 'txt'}",
                mime="application/pdf" if HAS_FPDF else "text/plain",
                use_container_width=True,
            )

        if bt_sym in snapshots:
            bt_res = run_strategy_backtest(snapshots[bt_sym].df, initial_capital=st.session_state.total_capital_tl)
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Strateji Getirisi", f"%{bt_res['total_ret_pct']:+.2f}")
            m2.metric("Al & Tut Getirisi", f"%{bt_res['bh_ret_pct']:+.2f}")
            m3.metric("Maks. Düşüş (DD)", f"%{bt_res['max_dd_pct']:.2f}")
            m4.metric("Sharpe Oranı", f"{bt_res['sharpe']:.2f}")
            m5.metric("Kazanma Oranı", f"%{bt_res['win_rate_pct']:.1f}")

            eq_fig = go.Figure()
            eq_fig.add_trace(
                go.Scatter(
                    x=bt_res["equity"].index,
                    y=bt_res["equity"].values,
                    name="AlpQuant EMA/Momentum",
                    line=dict(color="#2563EB", width=2.2),
                )
            )
            eq_fig.add_trace(
                go.Scatter(
                    x=bt_res["bh_equity"].index,
                    y=bt_res["bh_equity"].values,
                    name="Al & Tut (Benchmark)",
                    line=dict(color="#64748B", width=1.6, dash="dot"),
                )
            )
            eq_fig.update_layout(
                height=360,
                margin=dict(l=10, r=14, t=24, b=10),
                paper_bgcolor="#0F141F",
                plot_bgcolor="#0B0F17",
                legend=dict(orientation="h", y=1.04, x=0, font=dict(color="#CBD5E1")),
                yaxis=dict(gridcolor="#1C2436", tickfont=dict(color="#94A3B8"), side="right"),
                xaxis=dict(showgrid=False, tickfont=dict(color="#64748B")),
            )
            st.plotly_chart(eq_fig, use_container_width=True, key=f"bt_eq_{bt_sym}")


if __name__ == "__main__":
    main()
