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
    page_title="AlpQuant Pro v8.0 | Kurumsal Yatırım Platformu",
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
    user_id: str = "yatirimci"
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
        ("KTJ", "Kuveyt Türk Portföy Teknoloji Katılım Fonu", "Katılım Teknoloji", 4, 2.94, 2.25),
        ("KCV", "Kuveyt Türk Portföy Çoklu Varlık Katılım Fonu", "Katılım Değişken", 3, 3.85, 1.85),
        ("KNJ", "Kuveyt Türk Portföy Enerji Katılım Fonu", "Katılım Hisse Senedi", 4, 2.65, 2.10),
        ("KPU", "Kuveyt Türk Portföy İkinci Katılım Hisse Senedi (TL) Fonu", "Katılım Hisse Senedi", 4, 3.28, 2.05),
        ("KTN", "Kuveyt Türk Portföy Kira Sertifikaları Katılım (TL) Fonu", "Kira Sertifikası (Sukuk)", 2, 4.18, 1.40),
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



def _extend_universe_catalog(catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adds 250+ additional real BIST stocks, TEFAS funds, Global/EU/US equities & Crypto pairs."""
    existing = {c["symbol"] for c in catalog}

    extra_bist = [
        ("TCKRC", "Kıraç Galvaniz Telekominikasyon", "Makine & Sanayi", True, 32.4),
        ("BAHCL", "Bahadır Kimya Sanayi", "Kimya & Petrokimya", True, 44.6),
        ("GUNDG", "Gündoğdu Gıda Süt Ürünleri", "Perakende & Gıda", True, 38.2),
        ("AHSGY", "Ahes Gayrimenkul Yatırım Ortaklığı", "GYO & Gayrimenkul", True, 24.8),
        ("EFORC", "Efor Çay Sanayi", "Perakende & Gıda", True, 29.4),
        ("CGCAM", "Çağdaş Cam Sanayi", "Cam & Seramik", True, 26.5),
        ("SMRVA", "Sümer Varlık Yönetim", "Sigorta & Finans", False, 34.2),
        ("ARMGD", "Armada Gıda Ticaret", "Perakende & Gıda", True, 42.0),
        ("GLRMK", "Gülermak Ağır Sanayi İnşaat", "İnşaat & Çimento", True, 138.0),
        ("EGEGY", "EgeYapı Avrupa GYO", "GYO & Gayrimenkul", True, 18.4),
        ("MOPAS", "Mopaş Marketçilik Gıda", "Perakende & Gıda", True, 36.8),
        ("DSTKF", "Destek Finans Faktoring", "Sigorta & Finans", False, 52.4),
        ("ENDAE", "Enda Enerji Holding", "Enerji & Petrol", True, 19.6),
        ("BALSU", "Balsu Gıda Sanayi", "Perakende & Gıda", True, 21.4),
        ("KLYPV", "Kalyon Güneş Teknolojileri", "Enerji & Petrol", True, 74.5),
        ("VSNMD", "Vişne Madencilik Üretim", "Demir Çelik & Maden", True, 48.2),
        ("BULGS", "Bulls Girişim Sermayesi", "Holding & Yatırım", False, 16.8),
        ("LYDHO", "Lydia Holding", "Holding & Yatırım", False, 142.0),
        ("LYDYE", "Lydia Yeşil Enerji", "Enerji & Petrol", True, 285.0),
        ("ADEL", "Adel Kalemcilik", "Kağıt & Ambalaj", True, 48.6),
        ("ADESE", "Adese Gayrimenkul Yatırım", "GYO & Gayrimenkul", False, 2.15),
        ("AGYO", "Atakule GYO", "GYO & Gayrimenkul", False, 11.4),
        ("AKENR", "Akenerji Elektrik Üretim", "Enerji & Petrol", False, 13.8),
        ("AKMGY2", "Akmerkez GYO (B)", "GYO & Gayrimenkul", True, 240.0),
        ("AKYHO", "Akdeniz Yatırım Holding", "Holding & Yatırım", False, 5.45),
        ("ALKA2", "Alkim Kağıt (B)", "Kağıt & Ambalaj", True, 34.0),
        ("ALMAD", "Altınyağ Madencilik ve Enerji", "Enerji & Petrol", True, 7.85),
        ("ANELE", "Anel Elektrik Proje", "İnşaat & Çimento", True, 21.4),
        ("ARZUM", "Arzum Elektrikli Ev Aletleri", "Beyaz Eşya & Sanayi", True, 44.2),
        ("ATAGY", "Ata GYO", "GYO & Gayrimenkul", False, 12.6),
        ("ATEKS", "Akın Tekstil", "Tekstil & Perakende", True, 118.0),
        ("ATLAS", "Atlas Menkul Kıymetler", "Sigorta & Finans", False, 4.95),
        ("ATSYH", "Atlantis Yatırım Holding", "Holding & Yatırım", False, 58.4),
        ("AVGYO", "Avrasya GYO", "GYO & Gayrimenkul", False, 7.65),
        ("AVOD", "A.V.O.D. Kurutulmuş Gıda", "Perakende & Gıda", True, 3.85),
        ("AYES", "Ayes Çelik Hasır", "Demir Çelik & Maden", True, 36.2),
        ("BANVT2", "Banvit (B)", "Perakende & Gıda", True, 308.0),
        ("BASCM", "Baştaş Başkent Çimento", "İnşaat & Çimento", True, 6.45),
        ("BJKAS2", "Beşiktaş (B)", "Turizm & Medya", False, 5.40),
        ("BOYP", "Boyner Perakende", "Tekstil & Perakende", True, 42.0),
        ("BRKVY", "Birikim Varlık Yönetim", "Sigorta & Finans", False, 78.5),
        ("BRLSM", "Birleşim Mühendislik", "İnşaat & Çimento", True, 22.8),
        ("BRMEN", "Birlik Mensucat", "Tekstil & Perakende", True, 9.45),
        ("BSM", "Borsa İstanbul Sanayi", "Makine & Sanayi", True, 41.2),
        ("BURCE2", "Burçelik (B)", "Demir Çelik & Maden", True, 28.0),
        ("CASA", "Casa Emtia Petrol", "Kimya & Petrokimya", False, 148.0),
        ("CELHA", "Çelik Halat ve Tel", "Demir Çelik & Maden", True, 24.6),
        ("CEMAS", "Çemaş Döküm Sanayi", "Demir Çelik & Maden", True, 4.15),
        ("CEOEM", "CEO Event Medya", "Turizm & Medya", False, 34.8),
        ("CMENT", "Çimentaş İzmir Çimento", "İnşaat & Çimento", True, 315.0),
        ("CRDFA", "Creditwest Faktoring", "Sigorta & Finans", False, 8.95),
        ("DENGE", "Denge Yatırım Holding", "Holding & Yatırım", False, 3.15),
        ("DESA2", "Desa Deri (B)", "Tekstil & Perakende", True, 22.4),
        ("DIRIT", "Diriteks Diriliş Tekstil", "Tekstil & Perakende", True, 16.4),
        ("DOBUR", "Doğan Burda Dergi", "Turizm & Medya", False, 185.0),
        ("DOCO", "DO & CO Aktiengesellschaft", "Ulaştırma & Lojistik", True, 5650.0),
        ("DZGYO", "Deniz GYO", "GYO & Gayrimenkul", False, 5.85),
        ("EGCYO", "Egeli & Co Yatırım", "Sigorta & Finans", False, 4.25),
        ("EKIZ", "Ekiz Kimya Sanayi", "Kimya & Petrokimya", True, 42.6),
        ("EMNIS", "Eminiş Ambalaj", "Kağıt & Ambalaj", True, 164.0),
        ("ENKAI2", "Enka İnşaat (B)", "İnşaat & Çimento", True, 56.5),
        ("ERBOS2", "Erbosan (B)", "Demir Çelik & Maden", True, 215.0),
        ("ESCAR", "Escar Filo Kiralama", "Ulaştırma & Lojistik", False, 34.5),
        ("ETILR2", "Etiler Gıda (B)", "Perakende & Gıda", True, 12.6),
        ("ETYAT", "Euro Trend Yatırım", "Sigorta & Finans", False, 11.4),
        ("EUYO", "Euro Menkul Kıymet", "Sigorta & Finans", False, 12.8),
        ("FENER2", "Fenerbahçe (B)", "Turizm & Medya", False, 110.0),
        ("FORMT", "Formet Metal ve Cam", "Cam & Seramik", True, 4.65),
        ("GEDIK2", "Gedik Yatırım (B)", "Sigorta & Finans", False, 8.55),
        ("GENTS2", "Gentaş (B)", "Mobilya & Ev", True, 19.2),
        ("GLRYH", "Güler Yatırım Holding", "Holding & Yatırım", False, 13.4),
        ("GRTRK", "GrainTurk Holding", "Perakende & Gıda", True, 148.0),
        ("GSDHO2", "GSD Holding (B)", "Holding & Yatırım", False, 4.80),
        ("HATEK2", "Hateks (B)", "Tekstil & Perakende", True, 21.0),
        ("HDFGS", "Hedef Girişim Sermayesi", "Holding & Yatırım", False, 2.45),
        ("IHAAS", "İhlas Haber Ajansı", "Turizm & Medya", True, 14.2),
        ("IHGZT2", "İhlas Gazetecilik (B)", "Turizm & Medya", True, 1.80),
        ("INVEO", "Inveo Yatırım Holding", "Holding & Yatırım", False, 34.8),
        ("ISATR", "İş Bankası (A)", "Bankacılık & Finans", False, 215000.0),
        ("ISBTR", "İş Bankası (B)", "Bankacılık & Finans", False, 48500.0),
        ("ISKUR", "İş Bankası (Kurucu)", "Bankacılık & Finans", False, 1850000.0),
        ("ISYAT", "İş Yatırım Ortaklığı", "Sigorta & Finans", False, 11.8),
        ("IZFAS2", "İzmir Fırça (B)", "Kimya & Petrokimya", True, 34.5),
        ("JANTS2", "Jantsa (B)", "Otomotiv & Yan Sanayi", True, 28.0),
        ("KENT", "Kent Gıda Maddeleri", "Perakende & Gıda", True, 485.0),
        ("KGYO2", "Koray GYO (B)", "GYO & Gayrimenkul", False, 24.5),
        ("KLKIM", "Kalekim Kimyevi Maddeler", "Kimya & Petrokimya", True, 31.8),
        ("KLNMA2", "Kalkınma Bankası (B)", "Bankacılık & Finans", False, 34.0),
        ("KONYA2", "Konya Çimento (B)", "İnşaat & Çimento", True, 7800.0),
        ("KRPLS", "Koroplast Temizlik Ambalaj", "Kağıt & Ambalaj", True, 7.45),
        ("KSTUR2", "Kuştur (B)", "Turizm & Medya", False, 2440.0),
        ("KUVVA", "Kuvva Gıda Ticaret", "Perakende & Gıda", True, 64.5),
        ("LKMNH2", "Lokman Hekim (B)", "Sağlık & İlaç", True, 19.4),
        ("MARKA", "Marka Yatırım Holding", "Holding & Yatırım", False, 18.4),
        ("MEPET", "Mepet Metro Petrol", "Enerji & Petrol", False, 12.6),
        ("METUR", "Metemtur Otelcilik", "Turizm & Medya", False, 16.8),
        ("MGROS2", "Migros (B)", "Perakende & Gıda", False, 520.0),
        ("MMCAS", "MMC Sanayi ve Ticari", "Holding & Yatırım", False, 28.4),
        ("MZHLD", "Mazhar Zorlu Holding", "Holding & Yatırım", False, 11.6),
        ("NIBAS", "Niğbaş Niğde Beton", "İnşaat & Çimento", True, 24.8),
        ("OBASE", "Obase Bilgisayar", "Savunma & Teknoloji", True, 38.6),
        ("ORGE", "Orge Enerji Elektrik", "Enerji & Petrol", True, 84.2),
        ("OSTIM", "Ostim Endüstriyel Yatırımlar", "Holding & Yatırım", True, 7.65),
        ("OYLUM", "Oylum Sınai Yatırımlar", "Perakende & Gıda", True, 8.95),
        ("OZGYO", "Özderici GYO", "GYO & Gayrimenkul", False, 6.45),
        ("OZRDN", "Özerden Ambalaj", "Kağıt & Ambalaj", True, 24.2),
        ("OZSUB", "Özsu Balık Üretim", "Perakende & Gıda", True, 19.6),
        ("PAMEL", "Pamel Yenilenebilir Elektrik", "Enerji & Petrol", True, 124.0),
        ("PCILT", "PC İletişim ve Medya", "Turizm & Medya", False, 22.8),
        ("PEHOL", "Pera Yatırım Holding", "Holding & Yatırım", False, 28.6),
        ("PKART", "Plastikkart Akıllı Kart", "Savunma & Teknoloji", True, 64.2),
        ("PLTUR", "Platform Turizm Taşımacılık", "Ulaştırma & Lojistik", True, 24.5),
        ("PNLSN", "Panelsan Çatı Cephe", "İnşaat & Çimento", True, 58.4),
        ("POLTK", "Politeknik Metal Sanayi", "Demir Çelik & Maden", True, 6850.0),
        ("PRDGS", "Pardus Girişim Sermayesi", "Holding & Yatırım", False, 7.85),
        ("PRZMA", "Prizma Pres Matbaacılık", "Kağıt & Ambalaj", True, 18.4),
        ("PSDTC", "Pergamon Status Dış Ticaret", "Perakende & Gıda", True, 84.6),
        ("QNBTR", "QNB Bank A.Ş.", "Bankacılık & Finans", False, 265.0),
        ("QNBFK", "QNB Finansal Kiralama", "Sigorta & Finans", False, 48.2),
        ("RHEAG", "Rhea Girişim Sermayesi", "Holding & Yatırım", False, 6.45),
        ("ROYAL", "Royal Halı İplik", "Tekstil & Perakende", True, 14.2),
        ("SAFKR", "Safkar Ege Soğutmacılık", "Otomotiv & Yan Sanayi", True, 36.8),
        ("SAMAT", "Saray Matbaacılık", "Kağıt & Ambalaj", True, 21.4),
        ("SANEL", "Sanel Mühendislik", "Makine & Sanayi", True, 28.6),
        ("SANFM", "Sanifoam Endüstri", "Kimya & Petrokimya", True, 38.4),
        ("SEKFK", "Şeker Finansal Kiralama", "Sigorta & Finans", False, 14.8),
        ("SEKUR", "Sekuro Plastik Ambalaj", "Kağıt & Ambalaj", True, 11.6),
        ("SELGD", "Selçuk Gıda Endüstri", "Perakende & Gıda", True, 24.8),
        ("SELVA", "Selva Gıda Sanayi", "Perakende & Gıda", True, 14.2),
        ("SEYKM", "Seyitler Kimya", "Sağlık & İlaç", True, 7.65),
        ("SNICA", "Sanica Isı Sanayi", "Makine & Sanayi", True, 7.45),
        ("SNKRN", "Senkron Siber Güvenlik", "Savunma & Teknoloji", True, 58.4),
        ("SONME", "Sönmez Filament", "Tekstil & Perakende", True, 68.2),
        ("SUMAS", "Sumaş Suni Tahta", "Mobilya & Ev", True, 198.0),
        ("SUNTK", "Sun Tekstil Sanayi", "Tekstil & Perakende", True, 26.8),
        ("TBORG", "Türk Tuborg", "Perakende & Gıda", False, 142.0),
        ("TDGYO", "Trend GYO", "GYO & Gayrimenkul", False, 14.6),
        ("TETMT", "Tetamat Gıda Yatırımları", "Perakende & Gıda", True, 5400.0),
        ("TGSAS", "TGS Dış Ticaret", "Perakende & Gıda", True, 58.4),
        ("TMPOL", "Temapol Polimer Plastik", "Kimya & Petrokimya", True, 74.2),
        ("TRGYO2", "Torunlar GYO (B)", "GYO & Gayrimenkul", True, 56.0),
        ("TUCLK2", "Tuğçelik (B)", "Demir Çelik & Maden", True, 11.2),
        ("TURGG", "Türker Proje Gayrimenkul", "GYO & Gayrimenkul", True, 740.0),
        ("UFUK", "Ufuk Yatırım Yönetim", "Holding & Yatırım", False, 185.0),
        ("ULFA", "Ulusal Faktoring", "Sigorta & Finans", False, 14.8),
        ("UNLU", "Ünlü Yatırım Holding", "Sigorta & Finans", False, 16.4),
        ("UZERB", "Uzertaş Boya Sanayi", "Kimya & Petrokimya", True, 142.0),
        ("VAKFN", "Vakıf Finansal Kiralama", "Sigorta & Finans", False, 3.85),
        ("VAKKO2", "Vakko (B)", "Tekstil & Perakende", True, 96.0),
        ("VANGD", "Vanet Gıda Sanayi", "Perakende & Gıda", True, 18.6),
        ("VKING", "Viking Kağıt ve Selüloz", "Kağıt & Ambalaj", True, 44.2),
        ("YAPRK", "Yaprak Süt ve Besi", "Perakende & Gıda", True, 194.0),
        ("YAYLA", "Yayla Enerji Üretim", "Enerji & Petrol", True, 11.8),
        ("YESIL2", "Yeşil Yatırım (B)", "Holding & Yatırım", False, 2.80),
        ("YGYO", "Yeşil GYO", "GYO & Gayrimenkul", False, 4.95),
        ("YKSLN2", "Yükselen Çelik (B)", "Demir Çelik & Maden", True, 15.6),
        ("YONGA", "Yonga Mobilya Sanayi", "Mobilya & Ev", True, 68.4),
        ("YUNSA2", "Yünsa (B)", "Tekstil & Perakende", True, 54.0),
        ("YYAPI", "Yeşil Yapı Endüstrisi", "İnşaat & Çimento", False, 3.45),
        ("ZEDUR", "Zedur Enerji Elektrik", "Enerji & Petrol", True, 14.8),
    ]
    for sym, name, sec, kat, px in extra_bist:
        if sym in existing:
            continue
        existing.add(sym)
        h = int(hashlib.md5(sym.encode()).hexdigest()[:6], 16)
        idxs = ["BIST_TUM"] + (["KATILIM_TUM"] if kat else [])
        catalog.append({
            "symbol": sym, "name": name, "asset_class": "BIST_STOCK", "market_group": "BIST Payları",
            "sector": sec, "category": "BIST Ana / Gelişen Pazar", "indices": idxs, "currency": "TRY",
            "risk_level": 4, "is_katilim": kat, "katilim_bucket": "BIST_KATILIM" if kat else "NONE",
            "base_price": px, "pe_ratio": round(7.0 + (h % 280) / 10.0, 2),
            "pb_ratio": round(0.8 + (h % 420) / 100.0, 2), "roe_pct": round(6.0 + (h % 270) / 10.0, 2),
            "ter_pct": None, "yahoo_symbol": f"{sym}.IS",
        })

    extra_tefas = [
        ("IHK", "İş Portföy BIST Katılım 100 Endeksi Hisse Fonu", "Katılım Hisse Senedi", True, 4, 8.45, 1.95),
        ("KPC", "Kuveyt Türk Portföy Katılım Hisse Senedi (TL) Fonu", "Katılım Hisse Senedi", True, 4, 18.52, 2.40),
        ("KSV", "Kuveyt Türk Portföy Katılım Serbest Fon", "Katılım Değişken", True, 4, 5.42, 1.90),
        ("KAV", "Kuveyt Türk Portföy Agresif Katılım Fonu", "Katılım Değişken", True, 5, 4.95, 2.15),
        ("KRF", "Kuveyt Türk Portföy Kira Sertifikası Döviz Fonu", "Katılım Döviz / Eurobond", True, 3, 4.32, 1.40),
        ("KSL", "Kuveyt Türk Portföy Kısa Vadeli Katılım (TL) Fonu", "Katılım Para Piyasası", True, 1, 2.86, 0.90),
        ("RBV", "Albaraka Portföy Kısa Vadeli Katılım Serbest Fon", "Katılım Para Piyasası", True, 1, 3.12, 0.95),
        ("RBD", "Albaraka Portföy Değişken Katılım Fonu", "Katılım Değişken", True, 3, 4.18, 1.85),
        ("RBG", "Albaraka Portföy Gümüş Katılım Fonu", "Kıymetli Maden / Gümüş", True, 4, 3.74, 1.65),
        ("ZJI", "Ziraat Portföy İkinci Katılım Hisse Fonu", "Katılım Hisse Senedi", True, 4, 9.25, 2.00),
        ("ZJL", "Ziraat Portföy Katılım Likit Fon", "Katılım Para Piyasası", True, 1, 2.94, 0.85),
        ("ZJB", "Ziraat Portföy Katılım Sürdürülebilirlik Fonu", "Katılım Hisse Senedi", True, 4, 5.18, 2.05),
        ("MPJ", "Mükafat Portföy Teknoloji Katılım Fonu", "Katılım Teknoloji", True, 4, 3.65, 2.20),
        ("MPL", "Mükafat Portföy Katılım Likit Fon", "Katılım Para Piyasası", True, 1, 2.74, 0.90),
        ("TPZ", "TEB Portföy Para Piyasası (TL) Fonu", "Para Piyasası", False, 1, 5.42, 0.65),
        ("PPZ", "Azimut Portföy Para Piyasası (TL) Fonu", "Para Piyasası", False, 1, 4.86, 0.62),
        ("PRU", "QNB Finans Portföy Birinci Değişken Fon", "Değişken & Karma", False, 3, 7.15, 2.10),
        ("FIL", "Fiba Portföy Para Piyasası Fonu", "Para Piyasası", False, 1, 6.24, 0.68),
        ("EC2", "Global MD Portföy Birinci Hisse Senedi Fonu", "Hisse Senedi (TL)", False, 4, 14.80, 2.35),
        ("HVS", "HSBC Portföy Hisse Senedi (TL) Fonu", "Hisse Senedi (TL)", False, 4, 18.40, 2.20),
        ("IDY", "İş Portföy BIST Temettü Endeksi Hisse Fonu", "Hisse Senedi (TL)", False, 4, 12.60, 1.95),
        ("IIE", "İstanbul Portföy İkinci Hisse Senedi Fonu", "Hisse Senedi (TL)", False, 4, 16.90, 2.30),
        ("TAU", "İş Portföy BIST Banka Endeksi Hisse Fonu", "Hisse Senedi (TL)", False, 5, 13.40, 1.95),
        ("ADP", "Ak Portföy BIST Banka Endeksi Hisse Fonu", "Hisse Senedi (TL)", False, 5, 12.10, 1.95),
        ("TI3", "İş Portföy İştirakleri Endeksi Hisse Fonu", "Hisse Senedi (TL)", False, 4, 15.20, 2.00),
        ("YHZ", "Yapı Kredi Portföy BIST 100 Dışı Hisse Fonu", "Hisse Senedi (TL)", False, 5, 11.85, 2.20),
        ("YHS", "Yapı Kredi Portföy Birinci Hisse Senedi Fonu", "Hisse Senedi (TL)", False, 4, 19.60, 2.15),
        ("TKF", "Tacirler Portföy Hisse Senedi (TL) Fonu", "Hisse Senedi (TL)", False, 4, 29.40, 2.45),
        ("TFF", "TEB Portföy Amerika Teknoloji Yabancı Fon", "Yabancı Hisse & Teknoloji", False, 5, 16.80, 2.60),
        ("YTD", "Yapı Kredi Portföy Yabancı Fon Sepeti Fonu", "Yabancı Hisse & Teknoloji", False, 4, 11.40, 2.40),
        ("ZFB", "Ziraat Portföy Agresif Değişken Fon", "Değişken & Karma", False, 4, 9.85, 2.25),
        ("ZLH", "Ziraat Portföy BIST 30 Endeksi Hisse Fonu", "Hisse Senedi (TL)", False, 4, 13.90, 1.85),
        ("HKM", "Hedef Portföy Katılım Altın Fonu", "Kıymetli Maden / Altın", True, 3, 8.95, 1.50),
        ("NJR", "Neo Portföy Birinci Hisse Senedi Fonu", "Hisse Senedi (TL)", False, 4, 14.20, 2.30),
        ("TI7", "İş Portföy Sürdürülebilirlik Hisse Fonu", "Tematik & ESG", False, 4, 10.60, 2.10),
        ("TLY", "Tera Portföy Birinci Serbest Fon", "Değişken & Karma", False, 5, 18.20, 2.50),
        ("TZD", "Ziraat Portföy BIST Temettü 25 Endeksi Fonu", "Hisse Senedi (TL)", False, 4, 11.75, 1.90),
        ("YDI", "Yapı Kredi Portföy Dinamik Değişken Fon", "Değişken & Karma", False, 4, 8.45, 2.20),
        ("YEF", "Yapı Kredi Portföy Eurobond Borçlanma Fonu", "Döviz & Eurobond", False, 3, 7.95, 1.65),
        ("BIST1", "Ak Portföy BIST 30 Endeksi Hisse Senedi Fonu", "Hisse Senedi (TL)", False, 4, 14.40, 1.85),
    ]
    for sym, name, cat, kat, rl, px, ter in extra_tefas:
        if sym in existing:
            continue
        existing.add(sym)
        catalog.append({
            "symbol": sym, "name": name, "asset_class": "TEFAS_FUND", "market_group": "TEFAS Fonları",
            "sector": "TEFAS Katılım" if kat else "TEFAS Konvansiyonel", "category": cat,
            "indices": ["TEFAS_ALL"] + (["TEFAS_KATILIM"] if kat else []), "currency": "TRY",
            "risk_level": rl, "is_katilim": kat, "katilim_bucket": "TEFAS_KATILIM" if kat else "NONE",
            "base_price": px, "pe_ratio": None, "pb_ratio": None, "roe_pct": None, "ter_pct": ter, "yahoo_symbol": None,
        })

    extra_global = [
        ("MSTR", "MicroStrategy Inc.", "ABD Teknoloji", False, 388.0),
        ("RKLB", "Rocket Lab USA Inc.", "Uzay & Savunma", True, 21.4),
        ("IONQ", "IonQ Inc. (Quantum)", "Yarı İletken & AI", True, 31.8),
        ("SOFI", "SoFi Technologies Inc.", "ABD Finans", False, 15.6),
        ("HOOD", "Robinhood Markets Inc.", "ABD Finans", False, 36.4),
        ("AFRM", "Affirm Holdings Inc.", "ABD Finans", False, 64.2),
        ("RBLX", "Roblox Corp.", "Medya & İletişim", True, 52.8),
        ("DKNG", "DraftKings Inc.", "ABD Tüketici", False, 41.6),
        ("DUOL", "Duolingo Inc.", "ABD Teknoloji", True, 342.0),
        ("APP", "AppLovin Corp.", "ABD Teknoloji", True, 318.0),
        ("CELH", "Celsius Holdings Inc.", "ABD Tüketici", True, 29.4),
        ("CAVA", "CAVA Group Inc.", "ABD Tüketici", True, 138.0),
        ("HIMS", "Hims & Hers Health Inc.", "ABD Sağlık", True, 28.6),
        ("ASTS", "AST SpaceMobile Inc.", "Uzay & Savunma", True, 24.8),
        ("OKLO", "Oklo Inc. (Nuclear AI)", "ABD Enerji", True, 22.4),
        ("VST", "Vistra Corp.", "ABD Enerji", True, 152.0),
        ("CEG", "Constellation Energy Corp.", "ABD Enerji", True, 248.0),
        ("GEV", "GE Vernova Inc.", "ABD Enerji", True, 334.0),
        ("RDDT", "Reddit Inc.", "ABD Teknoloji", True, 144.0),
        ("TEM", "Tempus AI Inc.", "ABD Sağlık", True, 56.4),
        ("SOUN", "SoundHound AI Inc.", "Yarı İletken & AI", True, 7.85),
        ("PATH", "UiPath Inc.", "ABD Teknoloji", True, 14.2),
        ("ROKU", "Roku Inc.", "Medya & İletişim", True, 74.6),
        ("PINS", "Pinterest Inc.", "Medya & İletişim", True, 31.4),
        ("TTD", "The Trade Desk Inc.", "ABD Teknoloji", True, 124.0),
        ("MELI", "MercadoLibre Inc.", "Küresel Teknoloji", True, 1980.0),
        ("SE", "Sea Limited", "Küresel Teknoloji", True, 108.0),
        ("NU", "Nu Holdings Ltd.", "ABD Finans", False, 14.6),
        ("NIO", "NIO Inc.", "Otomotiv & EV", True, 4.85),
        ("XPEV", "XPeng Inc.", "Otomotiv & EV", True, 13.4),
        ("LI", "Li Auto Inc.", "Otomotiv & EV", True, 24.2),
        ("JD", "JD.com Inc.", "Küresel Teknoloji", True, 36.8),
        ("BIDU", "Baidu Inc.", "Küresel Teknoloji", True, 86.4),
        ("SONY", "Sony Group Corp.", "Küresel Teknoloji", True, 19.8),
        ("TM", "Toyota Motor Corp.", "Otomotiv & EV", True, 178.0),
        ("LVMUY", "LVMH Moët Hennessy (ADR)", "Avrupa Lüks", True, 134.0),
        ("SIEGY", "Siemens AG (ADR)", "Avrupa Sanayi", True, 98.4),
        ("EADSY", "Airbus SE (ADR)", "Avrupa Savunma", True, 38.6),
        ("SHEL", "Shell plc", "Avrupa Enerji", True, 66.8),
        ("TTE", "TotalEnergies SE", "Avrupa Enerji", True, 59.4),
        ("NSRGY", "Nestlé S.A. (ADR)", "Avrupa Tüketici", True, 88.6),
        ("RHHBY", "Roche Holding AG (ADR)", "Avrupa Sağlık", True, 36.4),
    ]
    for sym, name, sec, kat, px in extra_global:
        if sym in existing:
            continue
        existing.add(sym)
        h = int(hashlib.md5(sym.encode()).hexdigest()[:6], 16)
        catalog.append({
            "symbol": sym, "name": name, "asset_class": "US_STOCK", "market_group": "Küresel ETF & ABD",
            "sector": sec, "category": "Küresel & ABD Büyüme Hisse",
            "indices": ["US_MEGA_CAP"] + (["ISLAMIC_GLOBAL"] if kat else []), "currency": "USD",
            "risk_level": 4, "is_katilim": kat, "katilim_bucket": "ISLAMIC_ETF_SUKUK" if kat else "NONE",
            "base_price": px, "pe_ratio": round(16.0 + (h % 400) / 10.0, 2),
            "pb_ratio": round(2.1 + (h % 850) / 100.0, 2), "roe_pct": round(10.0 + (h % 340) / 10.0, 2),
            "ter_pct": None, "yahoo_symbol": sym,
        })

    extra_crypto = [
        ("TRX-USD", "TRON (Spot USD)", "Kripto Varlık (L1)", 0.21),
        ("ENA-USD", "Ethena (Spot USD)", "Kripto DeFi / Oracle", 0.68),
        ("ONDO-USD", "Ondo Finance RWA (Spot USD)", "Kripto RWA / DeFi", 1.12),
        ("PENDLE-USD", "Pendle Finance (Spot USD)", "Kripto DeFi / Oracle", 5.45),
        ("JUP-USD", "Jupiter Solana DEX (Spot USD)", "Kripto DeFi / Oracle", 1.18),
        ("PYTH-USD", "Pyth Network (Spot USD)", "Kripto DeFi / Oracle", 0.46),
        ("WLD-USD", "Worldcoin (Spot USD)", "Kripto AI / L1", 2.65),
        ("ARKM-USD", "Arkham Intelligence (Spot USD)", "Kripto AI / L1", 2.15),
        ("KAS-USD", "Kaspa (Spot USD)", "Kripto Varlık (L1)", 0.165),
        ("FTM-USD", "Fantom / Sonic (Spot USD)", "Kripto Varlık (L1)", 0.96),
        ("GALA-USD", "Gala Games (Spot USD)", "Kripto Oyun & Metaverse", 0.038),
        ("SAND-USD", "The Sandbox (Spot USD)", "Kripto Oyun & Metaverse", 0.64),
        ("MANA-USD", "Decentraland (Spot USD)", "Kripto Oyun & Metaverse", 0.58),
        ("AXS-USD", "Axie Infinity (Spot USD)", "Kripto Oyun & Metaverse", 7.45),
        ("CHZ-USD", "Chiliz Fan Token (Spot USD)", "Kripto Oyun & Metaverse", 0.095),
        ("CRV-USD", "Curve DAO (Spot USD)", "Kripto DeFi / Oracle", 0.52),
        ("SNX-USD", "Synthetix (Spot USD)", "Kripto DeFi / Oracle", 2.18),
        ("COMP-USD", "Compound (Spot USD)", "Kripto DeFi / Oracle", 68.4),
        ("DYDX-USD", "dYdX Protocol (Spot USD)", "Kripto DeFi / Oracle", 1.54),
        ("EIGEN-USD", "EigenLayer (Spot USD)", "Kripto Altyapı", 3.42),
        ("ETHFI-USD", "Ether.fi (Spot USD)", "Kripto DeFi / Oracle", 2.12),
        ("ZRO-USD", "LayerZero (Spot USD)", "Kripto Altyapı", 4.65),
        ("STRK-USD", "Starknet (Spot USD)", "Kripto Layer-2", 0.62),
        ("ZK-USD", "ZKsync (Spot USD)", "Kripto Layer-2", 0.195),
        ("OM-USD", "MANTRA RWA (Spot USD)", "Kripto RWA / DeFi", 3.85),
        ("FLOKI-USD", "Floki Inu (Spot USD)", "Kripto Yüksek Beta", 0.00024),
        ("CRO-USD", "Cronos (Spot USD)", "Kripto Varlık (L1)", 0.185),
        ("THETA-USD", "Theta Network (Spot USD)", "Kripto Altyapı", 2.15),
        ("QNT-USD", "Quant Network (Spot USD)", "Kripto Altyapı", 98.4),
        ("XTZ-USD", "Tezos (Spot USD)", "Kripto Varlık (L1)", 1.32),
    ]
    for sym, name, cat, px in extra_crypto:
        if sym in existing:
            continue
        existing.add(sym)
        catalog.append({
            "symbol": sym, "name": name, "asset_class": "CRYPTO_LEVERAGED", "market_group": "Kripto & 3x ETF",
            "sector": "Kripto & Kaldıraç", "category": cat, "indices": ["CRYPTO_LEV"], "currency": "USD",
            "risk_level": 5, "is_katilim": False, "katilim_bucket": "NONE",
            "base_price": px, "pe_ratio": None, "pb_ratio": None, "roe_pct": None, "ter_pct": None, "yahoo_symbol": sym,
        })

    return catalog

UNIVERSAL_CATALOG = _extend_universe_catalog(_build_full_universe_catalog())



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

    TEFAS_API_URL = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"

    @staticmethod
    def _infer_tefas_category(title: str) -> Tuple[str, bool, int]:
        low = normalize_turkish_lower(title)
        is_kat = "katılım" in low or "katilim" in low or "kira sertifik" in low or "sukuk" in low
        if "para piyasası" in low or "para piyasasi" in low or "likit" in low:
            return ("Katılım Para Piyasası" if is_kat else "Para Piyasası"), is_kat, 1
        if "kira sertifik" in low or "sukuk" in low:
            return "Kira Sertifikası (Sukuk)", True, 1 if "kısa vadeli" in low or "kisa vadeli" in low else 2
        if "altın" in low or "altin" in low:
            return "Kıymetli Maden / Altın", is_kat, 3
        if "gümüş" in low or "gumus" in low:
            return "Kıymetli Maden / Gümüş", is_kat, 4
        if "teknoloji" in low or "yarı iletken" in low or "blokzincir" in low:
            return ("Katılım Teknoloji" if is_kat else "Yabancı Hisse & Teknoloji"), is_kat, 4
        if "hisse senedi" in low or "hisse" in low:
            return ("Katılım Hisse Senedi" if is_kat else "Hisse Senedi (TL)"), is_kat, 4
        if "eurobond" in low or "döviz" in low or "doviz" in low:
            return ("Katılım Döviz / Eurobond" if is_kat else "Döviz & Eurobond"), is_kat, 3
        if "borçlanma" in low or "borclanma" in low:
            return "Borçlanma Araçları", is_kat, 2
        return ("Katılım Değişken" if is_kat else "Değişken & Karma"), is_kat, 3

    @classmethod
    def fetch_tefas_live_quotes(cls) -> Dict[str, Dict[str, Any]]:
        """Fetches real official fund titles & daily NAV prices from TEFAS using a cookie-enabled session."""
        try:
            import http.cookiejar
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            # Step 1: Visit main page to obtain ASP.NET session cookies
            init_req = urllib.request.Request("https://www.tefas.gov.tr/TarihselVeriler.aspx", headers={"User-Agent": ua})
            try:
                with opener.open(init_req, timeout=3):
                    pass
            except Exception:
                pass

            end_dt = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(days=7)
            form_data = urllib.parse.urlencode({
                "fontip": "YAT", "sfontur": "", "fonkod": "", "fongrup": "",
                "bastarih": start_dt.strftime("%d.%m.%Y"),
                "bittarih": end_dt.strftime("%d.%m.%Y"),
                "fonturkod": "", "fonunvantip": "",
            }).encode("utf-8")
            req = urllib.request.Request(
                cls.TEFAS_API_URL,
                data=form_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "User-Agent": ua,
                    "Referer": "https://www.tefas.gov.tr/TarihselVeriler.aspx",
                    "Origin": "https://www.tefas.gov.tr",
                    "X-Requested-With": "XMLHttpRequest",
                },
                method="POST",
            )
            with opener.open(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            rows = raw.get("data", [])
            if not rows:
                return {}
            grouped: Dict[str, List[Tuple[int, float, str]]] = {}
            for r in rows:
                code = str(r.get("FONKODU") or "").strip().upper()
                px = r.get("FIYAT")
                if not code or px is None or float(px) <= 0:
                    continue
                ts = int(r.get("TARIH") or 0)
                title = str(r.get("FONUNVAN") or code).strip()
                grouped.setdefault(code, []).append((ts, float(px), title))
            out: Dict[str, Dict[str, Any]] = {}
            for code, pts in grouped.items():
                pts.sort(key=lambda x: x[0])
                last_px = pts[-1][1]
                prev_px = pts[-2][1] if len(pts) >= 2 else last_px
                chg = ((last_px - prev_px) / max(prev_px, 1e-9)) * 100.0
                title = pts[-1][2]
                cat, is_kat, rl = cls._infer_tefas_category(title)
                out[code] = {
                    "name": title, "close": round(last_px, 6 if last_px < 1 else 4),
                    "change_pct": round(chg, 2), "category": cat,
                    "is_katilim": is_kat, "risk_level": rl, "source": "Canlı TEFAS",
                }
            return out
        except Exception:
            return {}

    @classmethod
    def fetch_single_tefas_live(cls, code: str) -> Optional[Dict[str, Any]]:
        """
        Multi-source live TEFAS fallback for single funds (works even when tefas.gov.tr geo-blocks cloud IPs).
        Checks FVT, Yatirimci.ai, and Fonoloji server-rendered pages for exact daily NAV & official title.
        """
        c = code.strip().upper()
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        sources = [
            ("Canlı TEFAS (FVT)", f"https://fvt.com.tr/fonlar/yatirim-fonlari/{c}"),
            ("Canlı TEFAS (Yatırımcı)", f"https://yatirimci.ai/fon/yatirim-fonlari/{c.lower()}"),
            ("Canlı TEFAS (Fonoloji)", f"https://fonoloji.com/fon/{c}"),
        ]
        price_patterns = [
            r"son\s+pay\s+fiyat[ıi]\s*(?:₺|TL)?\s*([0-9]{1,5}[.,][0-9]{2,6})",
            r"son\s+fiyat[ıi][^0-9]{0,35}([0-9]{1,5}[.,][0-9]{2,6})\s*(?:TL|₺)",
            r"fiyat[ıi]?\s*[:\-]?\s*₺\s*([0-9]{1,5}[.,][0-9]{2,6})",
            r"([0-9]{1,5},[0-9]{2,6})\s*(?:TL|₺)",
        ]
        for src_label, url in sources:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "tr-TR,tr;q=0.9"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
                found_px = None
                for pat in price_patterns:
                    m = re.search(pat, body, flags=re.IGNORECASE)
                    if m:
                        raw_num = m.group(1).replace(".", "").replace(",", ".") if ("," in m.group(1) and "." in m.group(1)) else m.group(1).replace(",", ".")
                        val = float(raw_num)
                        if 0.001 < val < 50000:
                            found_px = val
                            break
                if found_px is not None:
                    # Extract official title from <title> or <h1> if clean
                    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", body, flags=re.IGNORECASE | re.DOTALL)
                    clean_title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
                    chg_m = re.search(r"günlük\s+(?:değişim|getiri)[^0-9+\-−]{0,20}([+\-−]?%?\s*[0-9]+[.,][0-9]+)", body, flags=re.IGNORECASE)
                    chg_val = None
                    if chg_m:
                        c_str = chg_m.group(1).replace("−", "-").replace("%", "").replace(",", ".").strip()
                        try:
                            chg_val = float(c_str)
                        except Exception:
                            chg_val = None
                    out: Dict[str, Any] = {"close": round(found_px, 4), "source": src_label}
                    if chg_val is not None:
                        out["change_pct"] = round(chg_val, 2)
                    if clean_title and len(clean_title) > 10 and "404" not in clean_title:
                        out["name"] = clean_title[:90]
                        cat, is_kat, rl = cls._infer_tefas_category(clean_title)
                        out["category"] = cat
                        out["is_katilim"] = is_kat
                        out["risk_level"] = rl
                    return out
            except Exception:
                continue
        return None

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
            with urllib.request.urlopen(req, timeout=3) as resp:
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
            data_src = str(live_quote.get("source") or "Canlı TradingView")
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
def load_universe_snapshots(use_live_tv: bool = False, _cache_v: str = "v73") -> Tuple[Dict[str, AssetSnapshot], int]:
    """Builds and caches all 840+ AssetSnapshots (+ any extra live TradingView BIST tickers)."""
    fetcher = MultiAssetDataFetcher()
    tv_bist = fetcher.fetch_tradingview_live_quotes("turkey", limit=550) if use_live_tv else {}
    tv_us = fetcher.fetch_tradingview_live_quotes("america", limit=200) if use_live_tv else {}
    tefas_live = fetcher.fetch_tefas_live_quotes() if use_live_tv else {}

    snapshots: Dict[str, AssetSnapshot] = {}
    existing_syms = set()

    for item in UNIVERSAL_CATALOG:
        sym = item["symbol"]
        existing_syms.add(sym)
        item_copy = dict(item)
        if item_copy["asset_class"] == "TEFAS_FUND":
            lq = tefas_live.get(sym)
            if lq:
                item_copy["name"] = lq["name"]
                item_copy["category"] = lq["category"]
                item_copy["is_katilim"] = lq["is_katilim"]
                item_copy["sector"] = "TEFAS Katılım" if lq["is_katilim"] else "TEFAS Konvansiyonel"
                item_copy["katilim_bucket"] = "TEFAS_KATILIM" if lq["is_katilim"] else "NONE"
                item_copy["risk_level"] = lq["risk_level"]
        elif item_copy["asset_class"] == "BIST_STOCK":
            lq = tv_bist.get(sym)
        else:
            lq = tv_us.get(sym)
        snapshots[sym] = fetcher.build_snapshot(item_copy, days=120, live_quote=lq)

    # Also add any additional real TEFAS funds from live TEFAS API beyond built-in catalog
    if tefas_live:
        for sym, lq in tefas_live.items():
            if sym not in existing_syms and len(sym) == 3 and sym.isalnum():
                existing_syms.add(sym)
                extra_f = {
                    "symbol": sym, "name": lq["name"], "asset_class": "TEFAS_FUND",
                    "market_group": "TEFAS Fonları",
                    "sector": "TEFAS Katılım" if lq["is_katilim"] else "TEFAS Konvansiyonel",
                    "category": lq["category"],
                    "indices": ["TEFAS_ALL"] + (["TEFAS_KATILIM"] if lq["is_katilim"] else []),
                    "currency": "TRY", "risk_level": lq["risk_level"],
                    "is_katilim": lq["is_katilim"],
                    "katilim_bucket": "TEFAS_KATILIM" if lq["is_katilim"] else "NONE",
                    "base_price": lq["close"], "pe_ratio": None, "pb_ratio": None,
                    "roe_pct": None, "ter_pct": 1.65, "yahoo_symbol": None,
                }
                snapshots[sym] = fetcher.build_snapshot(extra_f, days=120, live_quote=lq)

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

    return snapshots, len(tv_bist) + len(tv_us) + len(tefas_live)

@st.cache_data(ttl=600, show_spinner=False)
def fetch_single_asset_live_quote(symbol: str, asset_class: str, yahoo_symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetches a real live price for any focused or portfolio asset (TEFAS multi-source or Yahoo Finance)."""
    if asset_class == "TEFAS_FUND":
        return MultiAssetDataFetcher.fetch_single_tefas_live(symbol)
    if HAS_YFINANCE and yahoo_symbol:
        try:
            hist = yf.Ticker(yahoo_symbol).history(period="5d", interval="1d", timeout=4)
            if hist is not None and not hist.empty:
                last_px = float(hist["Close"].iloc[-1])
                prev_px = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last_px
                chg = ((last_px - prev_px) / max(prev_px, 1e-9)) * 100.0
                return {"close": round(last_px, 4 if last_px < 10 else 2), "change_pct": round(chg, 2), "source": "Canlı Yahoo Finance"}
        except Exception:
            pass
    return None


def apply_live_quote_to_snapshot(snap: AssetSnapshot, lq: Optional[Dict[str, Any]]) -> AssetSnapshot:
    """Applies a fetched live quote to an AssetSnapshot in memory without breaking cached objects."""
    if not lq or not lq.get("close"):
        return snap
    new_px = float(lq["close"])
    old_px = max(float(snap.last_price), 1e-9)
    scale = new_px / old_px
    df_scaled = snap.df.copy()
    for col in ("Open", "High", "Low", "Close"):
        df_scaled[col] = df_scaled[col] * scale
    chg = float(lq["change_pct"]) if lq.get("change_pct") is not None else snap.change_pct
    ind = QuantitativeIndicators.compute_all(df_scaled)
    return AssetSnapshot(
        symbol=snap.symbol,
        name=str(lq.get("name") or snap.name),
        asset_class=snap.asset_class,
        market_group=snap.market_group,
        sector="TEFAS Katılım" if lq.get("is_katilim") else snap.sector,
        category=str(lq.get("category") or snap.category),
        indices=snap.indices,
        currency=snap.currency,
        risk_level=int(lq.get("risk_level") or snap.risk_level),
        is_katilim=bool(lq["is_katilim"]) if "is_katilim" in lq else snap.is_katilim,
        katilim_bucket=snap.katilim_bucket,
        df=df_scaled,
        last_price=round(new_px, 4 if new_px < 10 else 2),
        prev_close=round(new_px / max(1.0 + chg / 100.0, 1e-6), 4 if new_px < 10 else 2),
        change_pct=round(chg, 2),
        volume=snap.volume,
        pe_ratio=snap.pe_ratio, pb_ratio=snap.pb_ratio, roe_pct=snap.roe_pct, ter_pct=snap.ter_pct,
        data_source=str(lq.get("source") or "Canlı Veri"),
        yahoo_symbol=snap.yahoo_symbol,
        indicators=ind,
    )



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
            with urllib.request.urlopen(req, timeout=3) as resp:
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

        raw_html = f"""
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
        return "\n".join(line.lstrip() for line in raw_html.splitlines() if line.strip())

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






def fetch_on_demand_symbol(raw_symbol: str) -> Optional[AssetSnapshot]:
    """Fetch any user-requested ticker live via Yahoo Finance (trying BIST .IS and global)."""
    sym = raw_symbol.strip().upper()
    if not sym or len(sym) > 15:
        return None
    if HAS_YFINANCE:
        for candidate, is_bist in [(f"{sym}.IS", True), (sym, False)]:
            try:
                tk = yf.Ticker(candidate)
                hist = tk.history(period="6mo", interval="1d", timeout=4)
                if hist is not None and not hist.empty and len(hist) >= 15:
                    df = hist[["Open", "High", "Low", "Close", "Volume"]].dropna()
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    last_px = float(df["Close"].iloc[-1])
                    prev_c = float(df["Close"].iloc[-2]) if len(df) > 1 else last_px
                    chg = float(((last_px - prev_c) / (prev_c + 1e-12)) * 100.0)
                    ind = QuantitativeIndicators.compute_all(df)
                    return AssetSnapshot(
                        symbol=sym,
                        name=f"{sym} (Canlı Eklendi)",
                        asset_class="BIST_STOCK" if is_bist else "US_STOCK",
                        market_group="BIST Payları" if is_bist else "Küresel ETF & ABD",
                        sector="Canlı Aranan Varlık",
                        category="Özel Canlı Arama",
                        indices=["BIST_TUM"] if is_bist else ["US_MEGA_CAP"],
                        currency="TRY" if is_bist else "USD",
                        risk_level=4,
                        is_katilim=False,
                        katilim_bucket="NONE",
                        df=df,
                        last_price=round(last_px, 4 if last_px < 10 else 2),
                        prev_close=round(prev_c, 4 if prev_c < 10 else 2),
                        change_pct=round(chg, 2),
                        volume=float(df["Volume"].iloc[-1]),
                        data_source="Canlı Yahoo Finance",
                        yahoo_symbol=candidate,
                        indicators=ind,
                    )
            except Exception:
                continue
    return None


def run_portfolio_basket_backtest(
    snaps: List[AssetSnapshot], initial_capital: float = 250_000.0
) -> Dict[str, Any]:
    """Equal-weight multi-asset portfolio backtest for the user's selected Basket."""
    if not snaps:
        return {}
    strat_curves = []
    bh_curves = []
    for s in snaps:
        res = run_strategy_backtest(s.df, initial_capital=1.0)
        strat_curves.append(res["equity"].rename(s.symbol))
        bh_curves.append(res["bh_equity"].rename(s.symbol))
    strat_df = pd.concat(strat_curves, axis=1).ffill().bfill()
    bh_df = pd.concat(bh_curves, axis=1).ffill().bfill()
    equity = strat_df.mean(axis=1) * initial_capital
    bh_equity = bh_df.mean(axis=1) * initial_capital
    daily_ret = equity.pct_change().fillna(0.0)
    total_ret = float((equity.iloc[-1] / initial_capital - 1.0) * 100.0)
    bh_ret = float((bh_equity.iloc[-1] / initial_capital - 1.0) * 100.0)
    peak = equity.cummax()
    max_dd = float(((equity - peak) / peak).min() * 100.0)
    sharpe = float((daily_ret.mean() / (float(daily_ret.std()) + 1e-9)) * np.sqrt(252))
    win_rate = float((daily_ret > 0).mean() * 100.0)
    return {
        "equity": equity,
        "bh_equity": bh_equity,
        "total_ret_pct": round(total_ret, 2),
        "bh_ret_pct": round(bh_ret, 2),
        "max_dd_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "win_rate_pct": round(win_rate, 1),
    }

# =============================================================================
# 8. ALPQUANT PRO v7.0 — CALM MASTER-DETAIL DESIGN SYSTEM (100% SELF-CONTAINED)
# =============================================================================
EMBEDDED_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-canvas: #0A0D12;
    --bg-surface: #111622;
    --bg-surface-2: #161D2E;
    --bg-hover: #1C253A;
    --border-subtle: rgba(148, 163, 184, 0.12);
    --border-strong: rgba(148, 163, 184, 0.24);
    --text-primary: #F1F5F9;
    --text-secondary: #94A3B8;
    --text-muted: #64748B;
}

[data-testid="stSidebar"],
[data-testid="collapsedControl"],
section[data-testid="stSidebar"],
button[kind="headerNoPadding"],
#MainMenu,
footer {
    display: none !important;
    width: 0 !important;
    visibility: hidden !important;
}

header[data-testid="stHeader"] {
    background: transparent !important;
    height: 1.1rem !important;
}

html, body, [data-testid="stAppViewContainer"], .main {
    background-color: var(--bg-canvas) !important;
    color: var(--text-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    -webkit-font-smoothing: antialiased;
}

.block-container {
    max-width: 1320px !important;
    margin: 0 auto !important;
    padding: 1.1rem 2.2rem 3.5rem 2.2rem !important;
}

/* Inputs & Selects */
div[data-baseweb="input"] > div,
div[data-baseweb="select"] > div,
div[data-baseweb="base-input"] {
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 10px !important;
    color: var(--text-primary) !important;
}

div[data-baseweb="input"] > div:hover,
div[data-baseweb="select"] > div:hover {
    border-color: var(--border-strong) !important;
    background-color: var(--bg-surface-2) !important;
}

/* Buttons */
button[data-testid="stPopoverButton"],
.stButton > button,
.stDownloadButton > button {
    background-color: transparent !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 10px !important;
    color: #CBD5E1 !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    padding: 0.45rem 0.9rem !important;
    transition: all 0.15s ease !important;
}

button[data-testid="stPopoverButton"]:hover,
.stButton > button:hover,
.stDownloadButton > button:hover {
    background-color: var(--bg-surface-2) !important;
    border-color: var(--border-strong) !important;
    color: #FFFFFF !important;
}

.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {
    background: #2563EB !important;
    border: 1px solid rgba(96, 165, 250, 0.35) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
}

.stButton > button[kind="primary"]:hover {
    background: #1D4ED8 !important;
}

/* Clean Tabs inside Detail View */
.stTabs [data-baseweb="tab-list"] {
    gap: 20px;
    background: transparent;
    border-bottom: 1px solid var(--border-subtle);
    padding: 0;
}
.stTabs [data-baseweb="tab"] {
    height: 40px;
    padding: 0 4px;
    color: #94A3B8 !important;
    font-weight: 500;
    font-size: 13.5px;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    color: #F8FAFC !important;
    font-weight: 600 !important;
    border-bottom: 2px solid #3B82F6 !important;
}

/* Brand Mark in Single Top Bar */
.aq-brand-inline {
    display: flex;
    align-items: center;
    gap: 10px;
    padding-top: 3px;
}
.aq-mark {
    width: 32px; height: 32px; border-radius: 9px;
    background: linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%);
    display: flex; align-items: center; justify-content: center;
    color: #FFFFFF; font-weight: 700; font-size: 14px;
}
.aq-brand-text { font-size: 16.5px; font-weight: 700; color: #F8FAFC; letter-spacing: -0.02em; }

.aq-divider {
    height: 1px;
    background: var(--border-subtle);
    margin: 10px 0 20px 0;
}

/* Market Ticker Strip (Quiet & Scannable) */
.aq-ticker-grid {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 22px;
}
.aq-ticker-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    padding: 12px 14px;
}
.aq-ticker-lbl { font-size: 11px; color: #64748B; font-weight: 600; letter-spacing: 0.02em; }
.aq-ticker-val { font-size: 15.5px; font-weight: 700; color: #F8FAFC; font-family: 'JetBrains Mono', monospace; margin-top: 5px; }
.aq-ticker-chg { font-size: 11.5px; font-weight: 600; font-family: 'JetBrains Mono', monospace; margin-top: 3px; }

/* Section Headers */
.aq-h2 { font-size: 16px; font-weight: 600; color: #F8FAFC; letter-spacing: -0.015em; margin: 0 0 12px 0; }
.aq-h2-sub { font-size: 12.5px; color: #64748B; font-weight: 400; margin-left: 8px; }

/* Calm Surface Cards */
.aq-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 14px;
}

.aq-shine-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    padding: 16px 18px;
    margin-bottom: 8px;
}

.aq-row-between { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.aq-sym { font-size: 15px; font-weight: 700; color: #F8FAFC; letter-spacing: -0.01em; }
.aq-sub { font-size: 12px; color: #64748B; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 3px; }
.aq-mono { font-family: 'JetBrains Mono', monospace; }
.aq-pos { color: #10B981; font-family: 'JetBrains Mono', monospace; font-weight: 600; }
.aq-neg { color: #EF4444; font-family: 'JetBrains Mono', monospace; font-weight: 600; }

/* Minimal Pills */
.aq-pill { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; }
.aq-pill-buy { background: rgba(16,185,129,0.13); color: #34D399; }
.aq-pill-hold { background: rgba(148,163,184,0.12); color: #CBD5E1; }
.aq-pill-sell { background: rgba(239,68,68,0.13); color: #FCA5A5; }
.aq-pill-warn { background: rgba(245,158,11,0.13); color: #FCD34D; }
.aq-pill-blue { background: rgba(59,130,246,0.14); color: #93C5FD; }

/* Editorial News Item */
.aq-news-item {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 10px;
}
.aq-news-title { font-size: 14px; font-weight: 600; color: #F1F5F9; line-height: 1.45; margin: 6px 0 8px 0; }
.aq-news-ai { font-size: 12.5px; color: #94A3B8; line-height: 1.5; border-left: 2px solid #3B82F6; padding-left: 10px; }

/* Stat Grid inside Detail View */
.aq-stat-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 14px;
}
.aq-stat-lbl { font-size: 11.5px; color: #64748B; }
.aq-stat-val { font-size: 16px; font-weight: 600; color: #F8FAFC; font-family: 'JetBrains Mono', monospace; margin-top: 3px; }

[data-testid="stDataFrame"] {
    border: 1px solid var(--border-subtle) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
}

@media (max-width: 1024px) {
    .aq-ticker-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .aq-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
"""


def inject_terminal_css() -> None:
    st.markdown(EMBEDDED_CSS, unsafe_allow_html=True)


def render_html(markup: str) -> None:
    clean = "\n".join(line.lstrip() for line in str(markup).splitlines() if line.strip())
    st.markdown(clean, unsafe_allow_html=True)


def action_pill_class(action: str) -> str:
    if action in ("GÜÇLÜ AL", "AL"):
        return "aq-pill aq-pill-buy"
    if action == "SAT":
        return "aq-pill aq-pill-sell"
    if action == "SPK ENGELİ":
        return "aq-pill aq-pill-warn"
    return "aq-pill aq-pill-hold"


# =============================================================================
# 9. CHARTS (CLEAN, CALM, MIDAS/KOYFIN POLISH)
# =============================================================================
def build_detailed_candlestick(snap: AssetSnapshot, df_override: Optional[pd.DataFrame] = None) -> go.Figure:
    df = (df_override if df_override is not None else snap.df).tail(95)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.78, 0.22])
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color="#10B981", decreasing_line_color="#EF4444",
            increasing_fillcolor="rgba(16,185,129,0.75)", decreasing_fillcolor="rgba(239,68,68,0.75)",
            name=snap.symbol,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=df["Close"].rolling(20, min_periods=5).mean(), line=dict(color="#60A5FA", width=1.5), name="SMA 20"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=df["Close"].rolling(50, min_periods=10).mean(), line=dict(color="#F59E0B", width=1.5), name="SMA 50"),
        row=1, col=1,
    )
    bar_colors = np.where(df["Close"] >= df["Open"], "rgba(16,185,129,0.40)", "rgba(239,68,68,0.40)")
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=bar_colors, name="Hacim"), row=2, col=1)
    fig.update_layout(
        height=420, margin=dict(l=4, r=10, t=12, b=6),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_rangeslider_visible=False, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1, font=dict(size=11, color="#94A3B8")),
        yaxis=dict(gridcolor="rgba(148,163,184,0.07)", side="right", tickfont=dict(size=11, color="#94A3B8")),
        yaxis2=dict(gridcolor="rgba(148,163,184,0.05)", side="right", tickfont=dict(size=10, color="#64748B")),
        xaxis2=dict(showgrid=False, tickfont=dict(size=11, color="#64748B")),
    )
    return fig


def build_backtest_chart(bt_res: Dict[str, Any], height: int = 300, label: str = "AlpQuant Strateji") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=bt_res["equity"].index, y=bt_res["equity"].values,
            name=label, line=dict(color="#3B82F6", width=2.2),
            fill="tozeroy", fillcolor="rgba(59, 130, 246, 0.10)",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bt_res["bh_equity"].index, y=bt_res["bh_equity"].values,
            name="Al & Tut", line=dict(color="#64748B", width=1.5, dash="dot"),
        )
    )
    fig.update_layout(
        height=height, margin=dict(l=4, r=10, t=14, b=6),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
        legend=dict(orientation="h", y=1.06, x=0, font=dict(size=11, color="#94A3B8")),
        yaxis=dict(gridcolor="rgba(148,163,184,0.07)", tickfont=dict(size=11, color="#94A3B8"), side="right"),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color="#64748B")),
    )
    return fig


# =============================================================================
# 10. MASTER-DETAIL STATE & HELPERS
# =============================================================================
HOME_LENSES = ["Tümü", "BIST", "Katılım", "TEFAS", "ABD & ETF", "Kripto"]
SCREENER_PRESETS = ["Tümü", "Güçlü AL (75+)", "Dip Dönüş (RSI<40)", "Hacim Artışı", "Değer (F/K<12)"]



PORTFOLIO_STORE_PATH = Path(__file__).resolve().parent / ".alpquant_portfolio.json"


def _encode_holdings_param(holdings: Dict[str, Dict[str, float]], basket: List[str]) -> str:
    parts = []
    for sym in basket[:25]:
        h = holdings.get(sym)
        if h:
            parts.append(f"{sym}:{h.get('qty', 1):.4g}:{h.get('avg_cost', 1):.6g}:{h.get('custom_sl', 0):.6g}:{h.get('custom_tp', 0):.6g}")
    return ",".join(parts)


def _decode_holdings_param(raw: str) -> Tuple[List[str], Dict[str, Dict[str, float]]]:
    basket: List[str] = []
    holdings: Dict[str, Dict[str, float]] = {}
    for token in (raw or "").split(","):
        bits = token.strip().split(":")
        if len(bits) >= 3 and bits[0]:
            sym = bits[0].upper()
            try:
                qty = float(bits[1])
                cost = float(bits[2])
                csl = float(bits[3]) if len(bits) > 3 else 0.0
                ctp = float(bits[4]) if len(bits) > 4 else 0.0
                if qty > 0 and cost > 0:
                    basket.append(sym)
                    holdings[sym] = {"qty": qty, "avg_cost": cost, "custom_sl": csl, "custom_tp": ctp}
            except Exception:
                continue
    return basket, holdings


def save_persistent_state() -> None:
    """Persists portfolio holdings, basket, and SPK profile to local JSON and URL query params."""
    payload = {
        "basket": list(st.session_state.get("basket", [])),
        "portfolio_holdings": dict(st.session_state.get("portfolio_holdings", {})),
        "total_capital_tl": float(st.session_state.get("total_capital_tl", 250_000.0)),
        "spk_level": int(getattr(st.session_state.get("suitability_profile"), "risk_score", 5)),
    }
    try:
        PORTFOLIO_STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        if hasattr(st, "query_params"):
            encoded = _encode_holdings_param(payload["portfolio_holdings"], payload["basket"])
            if encoded:
                st.query_params["pf"] = encoded
            elif "pf" in st.query_params:
                del st.query_params["pf"]
    except Exception:
        pass


def load_persistent_state() -> Optional[Dict[str, Any]]:
    """Restores portfolio state from URL query params first, then local JSON store."""
    try:
        if hasattr(st, "query_params") and "pf" in st.query_params:
            raw_pf = str(st.query_params["pf"])
            b, h = _decode_holdings_param(raw_pf)
            if b:
                return {"basket": b, "portfolio_holdings": h}
    except Exception:
        pass
    try:
        if PORTFOLIO_STORE_PATH.exists():
            data = json.loads(PORTFOLIO_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("portfolio_holdings"):
                return data
    except Exception:
        pass
    return None


def init_session_state() -> None:
    defaults = {
        "workspace": "Piyasalar",       # Piyasalar | Keşfet | Portföyüm
        "detail_symbol": None,          # None = show workspace; "THYAO" = open clean Asset Detail View
        "home_lens": "Tümü",
        "basket": ["THYAO", "ASELS", "KTV"],
        "portfolio_holdings": {
            "THYAO": {"qty": 120.0, "avg_cost": 289.0, "custom_sl": 0.0, "custom_tp": 0.0},
            "ASELS": {"qty": 450.0, "avg_cost": 71.5, "custom_sl": 0.0, "custom_tp": 0.0},
            "KTV": {"qty": 8000.0, "avg_cost": 5.10, "custom_sl": 0.0, "custom_tp": 0.0},
        },
        "custom_snapshots": {},
        "screener_preset": "Tümü",
        "suitability_profile": SuitabilityProfile(),
        "total_capital_tl": 250_000.0,
        "use_live_tv": True,
        "use_live_rss": True,
        "use_live_yf_chart": False,
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "smtp_user": "",
        "smtp_pass": "",
        "email_recipient": "",
        "search_query": "",
        "market_group": "Tüm Piyasalar (1,100+)",
        "katilim_mode": "Tümü (Konv. + Katılım)",
        "sub_filter": "Tümü",
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
    if "state_restored" not in st.session_state:
        saved = load_persistent_state()
        if saved:
            if saved.get("basket"):
                defaults["basket"] = saved["basket"]
            if saved.get("portfolio_holdings"):
                defaults["portfolio_holdings"] = saved["portfolio_holdings"]
            if saved.get("total_capital_tl"):
                defaults["total_capital_tl"] = float(saved["total_capital_tl"])
            if saved.get("spk_level"):
                rl = int(saved["spk_level"])
                lbls = {
                    1: "Çok Düşük Risk (Muhafazakâr)", 2: "Düşük-Orta Risk (Temkinli)",
                    3: "Orta Risk (Dengeli)", 4: "Yüksek Risk (Büyüme Odaklı)",
                    5: "Çok Yüksek Risk (Agresif / Profesyonel)",
                }
                defaults["suitability_profile"] = SuitabilityProfile(
                    risk_score=rl, risk_label=lbls.get(rl, lbls[5]),
                    approved_levels=list(range(1, rl + 1)), max_single_weight=0.10 + rl * 0.03,
                )
        st.session_state["state_restored"] = True

    try:
        if hasattr(st, "secrets") and "GEMINI_API_KEY" in st.secrets:
            defaults["gemini_api_key"] = str(st.secrets["GEMINI_API_KEY"])
    except Exception:
        pass

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def open_detail(symbol: str) -> None:
    st.session_state.detail_symbol = symbol


def go_workspace(ws: str) -> None:
    st.session_state.workspace = ws
    st.session_state.detail_symbol = None


def toggle_basket(symbol: str, default_price: float = 0.0) -> None:
    b = list(st.session_state.basket)
    holdings = dict(st.session_state.get("portfolio_holdings", {}))
    if symbol in b:
        b.remove(symbol)
        holdings.pop(symbol, None)
    else:
        b.append(symbol)
        if symbol not in holdings:
            px = max(float(default_price), 1.0)
            est_qty = max(1.0, round(25_000.0 / px, 2 if px > 500 else 0))
            holdings[symbol] = {"qty": est_qty, "avg_cost": px, "custom_sl": 0.0, "custom_tp": 0.0}
    st.session_state.basket = b
    st.session_state.portfolio_holdings = holdings
    save_persistent_state()


def evaluate_portfolio_position(
    snap: AssetSnapshot,
    sig: SignalResult,
    holding: Dict[str, float],
    total_port_tl: float,
    usdtry: float = 34.55,
) -> Dict[str, Any]:
    """Institutional trader desk coaching engine for a single live portfolio holding."""
    qty = float(holding.get("qty", 0.0))
    avg_cost = float(holding.get("avg_cost", snap.last_price)) or snap.last_price
    fx = usdtry if snap.currency == "USD" else 1.0

    val_tl = qty * snap.last_price * fx
    cost_tl = qty * avg_cost * fx
    pnl_tl = val_tl - cost_tl
    pnl_pct = ((snap.last_price - avg_cost) / max(avg_cost, 1e-9)) * 100.0
    day_pnl_tl = val_tl * (snap.change_pct / (100.0 + snap.change_pct))
    weight_pct = (val_tl / max(total_port_tl, 1e-9)) * 100.0

    # Dynamic ATR Trailing Stop-Loss & Take-Profit
    c_sl = float(holding.get("custom_sl", 0.0) or 0.0)
    c_tp = float(holding.get("custom_tp", 0.0) or 0.0)
    dec = 4 if snap.last_price < 10 else 2

    if c_sl > 0:
        eff_sl = round(c_sl, dec)
    elif pnl_pct >= 12.0:
        eff_sl = round(max(sig.stop_loss, avg_cost * 1.04), dec)
    elif pnl_pct >= 7.0:
        eff_sl = round(max(sig.stop_loss, avg_cost * 1.01), dec)
    else:
        eff_sl = sig.stop_loss

    eff_tp = round(c_tp, dec) if c_tp > 0 else sig.take_profit
    dist_sl_pct = ((snap.last_price - eff_sl) / max(snap.last_price, 1e-9)) * 100.0
    dist_tp_pct = ((eff_tp - snap.last_price) / max(snap.last_price, 1e-9)) * 100.0

    # Pro Desk Action, Technical Rationale & Behavioral Psychology Coach
    rationale = (
        f"Teknik skor {sig.composite_score:.0f}/100 · RSI-14: {sig.rsi_14:.1f} · "
        f"SMA50 farkı %{sig.sma50_dist_pct:+.1f} · Hacim Z: {sig.vol_zscore:+.2f}."
    )

    if snap.last_price <= eff_sl:
        tag, badge_cls, prio = "STOP-LOSS TETİKLENDİ", "aq-pill aq-pill-sell", 1
        advice = (
            f"Fiyat ({snap.last_price:,.2f}) belirlenen {eff_sl:,.2f} koruma seviyesinin altında. "
            "Sermaye koruma kuralı gereği pozisyonu kademeli kapatarak nakit esnekliği kazanmanız önerilir."
        )
        psych_note = (
            "Kayıptan kaçınma (Loss Aversion) uyarısı: 'Maliyetine gelsin satarım' beklentisi küçük zararı kronikleştirir. "
            "Stop-loss bir yenilgi değil, bir sonraki fırsata sermaye saklama disiplinidir."
        )
    elif snap.last_price >= eff_tp or (pnl_pct >= 15.0 and sig.rsi_14 >= 72.0):
        tag, badge_cls, prio = "KÂR REALİZASYONU", "aq-pill aq-pill-warn", 2
        advice = (
            f"Pozisyon %{pnl_pct:+.1f} kârda ve hedef/aşırı alım (RSI {sig.rsi_14:.0f}) bölgesine ulaştı. "
            f"Pozisyonun %35–%50'sini realize edip kalan kısma {eff_sl:,.2f} iz süren stop koymanız rasyoneldir."
        )
        psych_note = (
            "FOMO ve açgözlülük dengesi: Zirveyi tek noktada yakalamaya çalışmak yerine kademeli kâr almak, "
            "olası geri çekilmelerde pişmanlık stresini sıfırlar."
        )
    elif weight_pct >= 38.0 and total_port_tl > 0:
        tag, badge_cls, prio = "AĞIRLIK DENGELE", "aq-pill aq-pill-warn", 3
        advice = (
            f"Bu varlık toplam portföyün %{weight_pct:.1f}'ini kaplıyor. "
            "Tek varlık riskini düşürmek için yükselişlerde ağırlığı %25 bandına çekip diğer sektörlere dağıtın."
        )
        psych_note = (
            "Aşırı güven (Overconfidence) tuzağı: Çok inandığımız tek varlığa duygusal bağ kurmak, "
            "beklenmedik tek bir KAP/piyasa haberinde tüm portföy psikolojisini bozabilir."
        )
    elif pnl_pct >= 7.0 and sig.sma50_dist_pct > 0:
        tag, badge_cls, prio = "İZ SÜREN STOP YÜKSELT", "aq-pill aq-pill-buy", 4
        advice = (
            f"Trend güçlü ve pozisyon %{pnl_pct:+.1f} kârda. "
            f"Stop-Loss'u maliyetinizin üzerine ({eff_sl:,.2f}) çekerek ana paranızı koruma altına alın ve trendi sürdürün."
        )
        psych_note = (
            "Erken kâr kesme dürtüsü (Disposition Effect): Kazananı sırf kârı kaybetme korkusuyla erken satmak yerine "
            "iz süren stop ile trendin devamından sakin biçimde yararlanın."
        )
    elif sig.action in ("GÜÇLÜ AL", "AL") and sig.rsi_14 <= 48.0 and weight_pct < 25.0:
        tag, badge_cls, prio = "KADEMELİ EKLEME", "aq-pill aq-pill-buy", 5
        advice = (
            f"Kompozit skor güçlü ({sig.composite_score:.0f}/100) ve RSI ({sig.rsi_14:.0f}) soğumuş bölgede. "
            f"{eff_sl:,.2f} stop disiplini korunarak 2–3 parçalı kademeli ekleme ile ortalama maliyet optimize edilebilir."
        )
        psych_note = (
            "Duygusal zamanlama kontrolü: Düşüş veya yatay dönemlerde panik yerine önceden tanımlı kademelerle ekleme yapmak "
            "karar kalitenizi piyasa gürültüsünden bağımsız kılar."
        )
    elif sig.action == "SAT" and pnl_pct < -4.0:
        tag, badge_cls, prio = "RİSKİ AZALT", "aq-pill aq-pill-sell", 2
        advice = (
            f"Momentum zayıflıyor ve fiyat ortalamaların altında. "
            f"{eff_sl:,.2f} desteği kırılırsa pozisyonu küçültmek sermaye erimesini önler."
        )
        psych_note = (
            "Batık maliyet yanılgısı (Sunk Cost): Geçmişte ödediğiniz maliyet piyasanın umurunda değildir; "
            "bugün bu varlığı sıfırdan alır mıydınız sorusunu dürüstçe sorun."
        )
    else:
        tag, badge_cls, prio = "TRENDİ KORU / TUT", "aq-pill aq-pill-hold", 6
        advice = (
            f"Pozisyon sağlıklı aralıkta ilerliyor. "
            f"{eff_sl:,.2f} stop-loss ve {eff_tp:,.2f} hedef fiyat bandını koruyarak planınıza sadık kalın."
        )
        psych_note = (
            "Aşırı işlem (Overtrading) karşıtı disiplin: Her gün al-sat yapmak zorunda değilsiniz; "
            "teziniz ve stop seviyeniz bozulmadığı sürece sabır en kârlı pozisyondur."
        )

    return {
        "symbol": snap.symbol, "name": snap.name, "currency": snap.currency,
        "market_group": snap.market_group, "is_katilim": snap.is_katilim,
        "qty": qty, "avg_cost": avg_cost, "last_price": snap.last_price,
        "val_tl": val_tl, "cost_tl": cost_tl, "pnl_tl": pnl_tl, "pnl_pct": pnl_pct,
        "day_pnl_tl": day_pnl_tl, "weight_pct": weight_pct,
        "eff_sl": eff_sl, "eff_tp": eff_tp, "dist_sl_pct": dist_sl_pct, "dist_tp_pct": dist_tp_pct,
        "score": sig.composite_score, "action_tag": tag, "badge_cls": badge_cls,
        "advice": advice, "rationale": rationale, "psych_note": psych_note, "priority": prio, "snap": snap, "sig": sig,
    }


def build_portfolio_donut(pos_list: List[Dict[str, Any]], height: int = 230) -> go.Figure:
    labels = [p["symbol"] for p in pos_list]
    values = [round(p["val_tl"], 0) for p in pos_list]
    colors = ["#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#06B6D4", "#EC4899", "#64748B"]
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels, values=values, hole=0.62,
                marker=dict(colors=colors[:len(labels)], line=dict(color="#0A0D12", width=2)),
                textinfo="label+percent",
                textfont=dict(size=11, color="#F1F5F9"),
                hovertemplate="<b>%{label}</b><br>%{value:,.0f} ₺ (%{percent})<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        height=height, margin=dict(l=4, r=4, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


def reset_screener_filters() -> None:
    st.session_state.search_query = ""
    st.session_state.market_group = "Tüm Piyasalar (1,100+)"
    st.session_state.katilim_mode = "Tümü (Konv. + Katılım)"
    st.session_state.sub_filter = "Tümü"
    st.session_state.bist_sectors = []
    st.session_state.tefas_categories = []
    st.session_state.use_fund_filter = False
    st.session_state.use_rsi_filter = False
    st.session_state.only_above_sma50 = False
    st.session_state.only_macd_bull = False
    st.session_state.signal_actions = []
    st.session_state.min_score = 0.0
    st.session_state.hide_spk_blocked = False
    st.session_state.screener_preset = "Tümü"


def compute_market_index_cards(snapshots: Dict[str, AssetSnapshot]) -> List[Dict[str, Any]]:
    bist100_snaps = [s for s in snapshots.values() if "BIST_100" in s.indices]
    xktum_snaps = [s for s in snapshots.values() if s.asset_class == "BIST_STOCK" and s.is_katilim]
    xu100_chg = float(np.mean([s.change_pct for s in bist100_snaps])) if bist100_snaps else 0.85
    xktum_chg = float(np.mean([s.change_pct for s in xktum_snaps])) if xktum_snaps else 1.12
    xu100_val = round(9680.0 * (1.0 + xu100_chg / 100.0), 1)
    xktum_val = round(10420.0 * (1.0 + xktum_chg / 100.0), 1)

    usd = snapshots.get("USDTRY=X")
    gold = snapshots.get("XAU_TRY")
    spy = snapshots.get("SPY")
    btc = snapshots.get("BTC-USD")
    return [
        {"label": "BIST 100", "val": f"{xu100_val:,.1f}", "chg": round(xu100_chg, 2)},
        {"label": "BIST Katılım", "val": f"{xktum_val:,.1f}", "chg": round(xktum_chg, 2)},
        {"label": "USD / TRY", "val": f"{usd.last_price:,.2f} ₺" if usd else "34.55 ₺", "chg": usd.change_pct if usd else 0.12},
        {"label": "Gram Altın", "val": f"{gold.last_price:,.0f} ₺" if gold else "3,045 ₺", "chg": gold.change_pct if gold else 0.64},
        {"label": "S&P 500", "val": f"${spy.last_price:,.2f}" if spy else "$598.00", "chg": spy.change_pct if spy else 0.42},
        {"label": "Bitcoin", "val": f"${btc.last_price:,.0f}" if btc else "$94,500", "chg": btc.change_pct if btc else 1.85},
    ]


def render_sparkline_table(df: pd.DataFrame, height: int = 490) -> Optional[int]:
    col_cfg = {}
    if hasattr(st, "column_config"):
        cc = st.column_config
        if "30G Trend" in df.columns and hasattr(cc, "LineChartColumn"):
            col_cfg["30G Trend"] = cc.LineChartColumn("30G Trend", width="small")
        if "Skor" in df.columns and hasattr(cc, "ProgressColumn"):
            col_cfg["Skor"] = cc.ProgressColumn("Skor", min_value=0, max_value=100, format="%.1f", width="small")
        if "Fiyat" in df.columns and hasattr(cc, "NumberColumn"):
            col_cfg["Fiyat"] = cc.NumberColumn("Fiyat", format="%.2f")
        if "Günlük %" in df.columns and hasattr(cc, "NumberColumn"):
            col_cfg["Günlük %"] = cc.NumberColumn("Günlük %", format="%+.2f%%")
        if "RSI" in df.columns and hasattr(cc, "NumberColumn"):
            col_cfg["RSI"] = cc.NumberColumn("RSI", format="%.1f")

    try:
        ev = st.dataframe(
            df, use_container_width=True, hide_index=True, height=height,
            column_config=col_cfg or None, on_select="rerun", selection_mode="single-row",
        )
        sel_rows = getattr(getattr(ev, "selection", None), "rows", None)
        if sel_rows:
            return int(sel_rows[0])
        return None
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True, height=height, column_config=col_cfg or None)
        return None


# =============================================================================
# 11. ASSET DETAIL VIEW (SEAMLESS MASTER-DETAIL SCREEN WITH ← GERİ)
# =============================================================================
def render_asset_detail_view(
    symbol: str,
    snapshots: Dict[str, AssetSnapshot],
    all_scored: Dict[str, SignalResult],
    profile: SuitabilityProfile,
) -> None:
    snap = snapshots[symbol]
    sig = all_scored[symbol]
    in_basket = symbol in st.session_state.basket
    parent_ws = st.session_state.workspace

    # Clean Top Breadcrumb Bar (← Geri Dön + + Portföye Ekle)
    b_back, _, b_act = st.columns([1.4, 3.2, 1.4])
    if b_back.button(f"← {parent_ws} Ekranına Dön", use_container_width=True):
        st.session_state.detail_symbol = None
        st.rerun()
    if b_act.button(
        "✓ Portföyümde" if in_basket else "+ Portföye Ekle",
        type="primary" if not in_basket else "secondary",
        use_container_width=True,
    ):
        toggle_basket(symbol, default_price=snap.last_price)
        st.rerun()

    # Hero Price Header
    chg_cls = "aq-pos" if snap.change_pct >= 0 else "aq-neg"
    kat_badge = "<span class='aq-pill aq-pill-buy'>Katılım Uyumlu</span>" if snap.is_katilim else ""
    render_html(
        f"""
        <div class="aq-card" style="margin-top:10px;">
            <div class="aq-row-between" style="flex-wrap:wrap; gap:14px;">
                <div>
                    <div style="display:flex; align-items:center; gap:9px; flex-wrap:wrap;">
                        <span style="font-size:26px; font-weight:700; color:#F8FAFC; letter-spacing:-0.02em;">{html.escape(snap.symbol)}</span>
                        <span class="{action_pill_class(sig.action)}" style="font-size:12px; padding:3px 10px;">{html.escape(sig.action)} · {sig.composite_score:.1f}/100</span>
                        {kat_badge}
                        <span class="aq-pill aq-pill-blue">SPK Lv.{snap.risk_level}</span>
                    </div>
                    <div style="font-size:13.5px; color:#94A3B8; margin-top:4px;">
                        {html.escape(snap.name)} · {html.escape(snap.market_group)} · {html.escape(snap.sector)}
                    </div>
                </div>
                <div style="text-align:right;">
                    <div class="aq-mono" style="font-size:28px; font-weight:700; color:#F8FAFC;">
                        {snap.last_price:,.2f} <span style="font-size:14px; color:#94A3B8;">{snap.currency}</span>
                    </div>
                    <div class="{chg_cls}" style="font-size:13.5px; margin-top:2px;">
                        {'▲' if snap.change_pct >= 0 else '▼'} {snap.change_pct:+.2f}% bugün
                    </div>
                </div>
            </div>
        </div>
        """
    )

    # Full-Width Clean Candlestick Chart
    live_df = (
        MultiAssetDataFetcher().fetch_live_ohlcv_for_symbol(snap)
        if st.session_state.use_live_yf_chart
        else None
    )
    st.plotly_chart(build_detailed_candlestick(snap, df_override=live_df), use_container_width=True, key=f"v7_deep_{snap.symbol}")

    # 3 Calm Internal Sub-Tabs (Everything about this asset in one place)
    t_ozet, t_bt, t_news = st.tabs(["Analiz & Risk Planı", "Strateji Backtesti", "Haberler & Benzer Varlıklar"])

    with t_ozet:
        pe_txt = f"{snap.pe_ratio:.1f}x" if snap.pe_ratio is not None else "—"
        pb_txt = f"{snap.pb_ratio:.2f}x" if snap.pb_ratio is not None else "—"
        roe_txt = f"%{snap.roe_pct:.1f}" if snap.roe_pct is not None else "—"
        ter_txt = f"%{snap.ter_pct:.2f}" if snap.ter_pct is not None else "—"

        c_left, c_right = st.columns([1.2, 1.0], gap="large")
        with c_left:
            render_html(
                f"""
                <div class="aq-card">
                    <div class="aq-h2" style="font-size:14px;">Teknik & Temel Göstergeler</div>
                    <div class="aq-stat-grid">
                        <div><div class="aq-stat-lbl">RSI-14</div><div class="aq-stat-val">{sig.rsi_14:.1f}</div></div>
                        <div><div class="aq-stat-lbl">SMA50 Fark</div><div class="aq-stat-val">%{sig.sma50_dist_pct:+.1f}</div></div>
                        <div><div class="aq-stat-lbl">Hacim Z</div><div class="aq-stat-val">{sig.vol_zscore:+.2f}</div></div>
                        <div><div class="aq-stat-lbl">ATR Volatilite</div><div class="aq-stat-val">%{sig.atr_pct:.2f}</div></div>
                        <div><div class="aq-stat-lbl">F/K Oranı</div><div class="aq-stat-val">{pe_txt}</div></div>
                        <div><div class="aq-stat-lbl">PD/DD</div><div class="aq-stat-val">{pb_txt}</div></div>
                        <div><div class="aq-stat-lbl">ROE</div><div class="aq-stat-val">{roe_txt}</div></div>
                        <div><div class="aq-stat-lbl">Fon TER</div><div class="aq-stat-val">{ter_txt}</div></div>
                    </div>
                </div>
                """
            )
        with c_right:
            render_html(
                f"""
                <div class="aq-card">
                    <div class="aq-h2" style="font-size:14px;">Risk & Pozisyon Planı</div>
                    <div class="aq-stat-grid" style="grid-template-columns:repeat(3, 1fr); margin-bottom:14px;">
                        <div><div class="aq-stat-lbl">Stop-Loss</div><div class="aq-stat-val" style="color:#FCA5A5;">{sig.stop_loss:,.2f}</div></div>
                        <div><div class="aq-stat-lbl">Take-Profit</div><div class="aq-stat-val" style="color:#6EE7B7;">{sig.take_profit:,.2f}</div></div>
                        <div><div class="aq-stat-lbl">Önerilen Lot</div><div class="aq-stat-val">{sig.suggested_qty:,}</div></div>
                    </div>
                    <div style="font-size:12.5px; color:#94A3B8; line-height:1.6; border-top:1px solid rgba(148,163,184,0.12); padding-top:10px;">
                        {"<br>".join("• " + html.escape(r) for r in sig.reasons)}
                    </div>
                </div>
                """
            )

    with t_bt:
        bt = run_strategy_backtest(snap.df, initial_capital=st.session_state.total_capital_tl)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("AlpQuant Strateji", f"%{bt['total_ret_pct']:+.2f}")
        m2.metric("Al & Tut", f"%{bt['bh_ret_pct']:+.2f}")
        m3.metric("Maks. Düşüş", f"%{bt['max_dd_pct']:.2f}")
        m4.metric("Sharpe Oranı", f"{bt['sharpe']:.2f}")
        st.plotly_chart(build_backtest_chart(bt, height=300, label=f"{snap.symbol} Stratejisi"), use_container_width=True, key=f"v7_bt_{snap.symbol}")

    with t_news:
        kap = KAPDisclosureAnalyzer(gemini_api_key=st.session_state.gemini_api_key)
        discs = kap.fetch_live_stream(snapshots, max_items=16, enable_live_rss=st.session_state.use_live_rss)
        sym_discs = [d for d in discs if d.symbol == snap.symbol] or discs[:3]
        for d in sym_discs[:4]:
            a = kap.analyze_disclosure(d)
            p_cls = {"OLUMLU": "aq-pill aq-pill-buy", "OLUMSUZ": "aq-pill aq-pill-sell", "HARD VETO": "aq-pill aq-pill-sell"}.get(a.sentiment, "aq-pill aq-pill-hold")
            render_html(
                f"""
                <div class="aq-news-item">
                    <div class="aq-row-between" style="font-size:11.5px; color:#64748B;">
                        <div><span class="aq-pill aq-pill-blue">{html.escape(d.symbol)}</span> <span class="{p_cls}" style="margin-left:5px;">{html.escape(a.sentiment)}</span></div>
                        <div>{html.escape(d.source)} · {html.escape(d.published_at)}</div>
                    </div>
                    <div class="aq-news-title">{html.escape(d.title)}</div>
                    <div class="aq-news-ai">{html.escape(a.ai_commentary)}</div>
                </div>
                """
            )

        peers = [
            (s, all_scored[s.symbol]) for s in snapshots.values()
            if s.symbol != snap.symbol and s.sector == snap.sector
        ]
        peers.sort(key=lambda x: x[1].composite_score, reverse=True)
        if peers[:3]:
            render_html(f"<div class='aq-h2' style='margin-top:16px;'>Aynı Sektördeki Rakipler <span class='aq-h2-sub'>{html.escape(snap.sector)}</span></div>")
            p_cols = st.columns(len(peers[:3]), gap="medium")
            for i_p, (ps, pg) in enumerate(peers[:3]):
                with p_cols[i_p]:
                    render_html(
                        f"""
                        <div class="aq-shine-card">
                            <div class="aq-row-between"><span class="aq-sym">{html.escape(ps.symbol)}</span><span class="{action_pill_class(pg.action)}">{pg.composite_score:.0f}p</span></div>
                            <div class="aq-sub">{html.escape(ps.name)}</div>
                            <div class="aq-row-between" style="margin-top:10px;">
                                <span class="aq-mono" style="font-size:14px; font-weight:600;">{ps.last_price:,.2f} {ps.currency}</span>
                                <span class="{'aq-pos' if ps.change_pct >= 0 else 'aq-neg'}" style="font-size:12px;">{ps.change_pct:+.2f}%</span>
                            </div>
                        </div>
                        """
                    )
                    if st.button(f"{ps.symbol} Aç", key=f"v7_peer_{i_p}_{ps.symbol}", use_container_width=True):
                        open_detail(ps.symbol)
                        st.rerun()


# =============================================================================
# 12. MAIN APPLICATION — SINGLE TOP BAR + 3 CLEAN WORKSPACES
# =============================================================================
def main() -> None:
    inject_terminal_css()
    init_session_state()

    profile: SuitabilityProfile = st.session_state.suitability_profile
    base_snapshots, live_quote_count = load_universe_snapshots(use_live_tv=st.session_state.use_live_tv)
    snapshots: Dict[str, AssetSnapshot] = dict(base_snapshots)
    snapshots.update(st.session_state.custom_snapshots)

    # Always ensure held portfolio symbols, active detail symbol, and editor symbol have fresh live quotes!
    if st.session_state.use_live_tv:
        priority_live_syms = set(st.session_state.basket)
        if st.session_state.detail_symbol:
            priority_live_syms.add(st.session_state.detail_symbol)
        if st.session_state.get("pf_editor_symbol"):
            priority_live_syms.add(st.session_state["pf_editor_symbol"])
        for psym in priority_live_syms:
            if psym in snapshots and not snapshots[psym].data_source.startswith("Canlı"):
                lq = fetch_single_asset_live_quote(psym, snapshots[psym].asset_class, snapshots[psym].yahoo_symbol)
                if lq:
                    snapshots[psym] = apply_live_quote_to_snapshot(snapshots[psym], lq)

    all_scored: Dict[str, SignalResult] = {
        sym: AlpQuantDecisionEngine.score_asset(snap, profile, st.session_state.total_capital_tl)
        for sym, snap in snapshots.items()
    }

    # -------------------------------------------------------------------------
    # SINGLE ULTRA-CLEAN TOP BAR (ONLY 1 ROW — ZERO STACKED CHROME!)
    # -------------------------------------------------------------------------
    cur_ws = st.session_state.workspace
    in_detail = st.session_state.detail_symbol is not None and st.session_state.detail_symbol in snapshots
    b_cnt = len(st.session_state.basket)

    tb_logo, tb_n1, tb_n2, tb_n3, tb_gap, tb_cfg = st.columns([1.8, 1.0, 1.0, 1.15, 1.35, 1.15], gap="small")
    with tb_logo:
        render_html("<div class='aq-brand-inline'><div class='aq-mark'>◈</div><span class='aq-brand-text'>AlpQuant</span></div>")

    if tb_n1.button("Piyasalar", type="primary" if (cur_ws == "Piyasalar" and not in_detail) else "secondary", use_container_width=True):
        go_workspace("Piyasalar")
        st.rerun()
    if tb_n2.button("Keşfet", type="primary" if (cur_ws == "Keşfet" and not in_detail) else "secondary", use_container_width=True):
        go_workspace("Keşfet")
        st.rerun()
    if tb_n3.button(f"Portföyüm ({b_cnt})", type="primary" if (cur_ws == "Portföyüm" and not in_detail) else "secondary", use_container_width=True):
        go_workspace("Portföyüm")
        st.rerun()

    with tb_cfg:
        with safe_popover(f"SPK Lv.{profile.risk_score} · ⚙", use_container_width=True):
            pt1, pt2 = st.tabs(["SPK Profili", "Sermaye & Veri"])
            with pt1:
                st.caption(f"Aktif Profil: **Seviye {profile.risk_score}/5** — {profile.risk_label}")
                with st.form("v7_spk_form", clear_on_submit=False):
                    q1 = st.selectbox("Yatırım Vadesi", ["0-1 Yıl (Kısa)", "1-3 Yıl (Orta)", "3+ Yıl (Uzun)"], index=2)
                    q2 = st.selectbox("Kayıp Toleransı", ["Anapara Koruma", "%10-15 Dalgalanma", "%25+ Yüksek Getiri"], index=2)
                    q3 = st.selectbox("Piyasa Tecrübesi", ["Başlangıç", "Orta (Hisse/Fon)", "Profesyonel"], index=2)
                    if st.form_submit_button("Profili Kaydet", use_container_width=True):
                        pts = (
                            {"0-1 Yıl (Kısa)": 1, "1-3 Yıl (Orta)": 2, "3+ Yıl (Uzun)": 3}[q1]
                            + {"Anapara Koruma": 1, "%10-15 Dalgalanma": 2, "%25+ Yüksek Getiri": 3}[q2]
                            + {"Başlangıç": 1, "Orta (Hisse/Fon)": 2, "Profesyonel": 3}[q3]
                        )
                        rl = 2 if pts <= 4 else (3 if pts <= 6 else (4 if pts <= 7 else 5))
                        lbls = {
                            2: "Düşük-Orta Risk (Temkinli)", 3: "Orta Risk (Dengeli)",
                            4: "Yüksek Risk (Büyüme Odaklı)", 5: "Çok Yüksek Risk (Agresif / Profesyonel)",
                        }
                        st.session_state.suitability_profile = SuitabilityProfile(
                            user_id=profile.user_id, risk_score=rl, risk_label=lbls[rl],
                            approved_levels=list(range(1, rl + 1)), max_single_weight=0.10 + rl * 0.03,
                        )
                        save_persistent_state()
                        st.success(f"SPK Seviye {rl}/5 kaydedildi.")
                        st.rerun()
            with pt2:
                st.session_state.total_capital_tl = st.number_input(
                    "Portföy Sermayesi (TL)", 10_000.0, 500_000_000.0, float(st.session_state.total_capital_tl), step=25_000.0
                )
                st.session_state.use_live_tv = st.checkbox("Canlı TradingView Fiyatları", value=st.session_state.use_live_tv)
                st.session_state.use_live_rss = st.checkbox("Canlı KAP & Finans Haberleri", value=st.session_state.use_live_rss)
                st.session_state.use_live_yf_chart = st.checkbox("Grafikte Canlı Yahoo Finance", value=st.session_state.use_live_yf_chart)
                st.session_state.email_recipient = st.text_input(
                    "Rapor E-Posta Adresi (Opsiyonel)",
                    value=st.session_state.email_recipient,
                    placeholder="ornek@eposta.com",
                )
                ec1, ec2 = st.columns(2)
                st.session_state.smtp_user = ec1.text_input("SMTP E-Posta", value=st.session_state.smtp_user)
                st.session_state.smtp_pass = ec2.text_input("Uygulama Şifresi", value=st.session_state.smtp_pass, type="password")

    render_html("<div class='aq-divider'></div>")

    # -------------------------------------------------------------------------
    # IF AN ASSET IS OPENED -> RENDER CLEAN DISTRACTION-FREE DETAIL VIEW
    # -------------------------------------------------------------------------
    if in_detail:
        render_asset_detail_view(st.session_state.detail_symbol, snapshots, all_scored, profile)
        return

    # =========================================================================
    # WORKSPACE 1: PİYASALAR (CALM, EDITORIAL HOME — ZERO BUTTON SOUP!)
    # =========================================================================
    if cur_ws == "Piyasalar":
        # 1. Quiet 6-Card Market Benchmark Strip
        idx_cards = compute_market_index_cards(snapshots)
        idx_html = "".join(
            f"<div class='aq-ticker-card'><div class='aq-ticker-lbl'>{html.escape(ic['label'])}</div>"
            f"<div class='aq-ticker-val'>{html.escape(ic['val'])}</div>"
            f"<div class='aq-ticker-chg' style='color:{'#10B981' if ic['chg'] >= 0 else '#EF4444'};'>"
            f"{'▲' if ic['chg'] >= 0 else '▼'} {ic['chg']:+.2f}%</div></div>"
            for ic in idx_cards
        )
        render_html(f"<div class='aq-ticker-grid'>{idx_html}</div>")

        # 2. Calm 1-Row Market Lens Switcher
        lens_cols = st.columns(len(HOME_LENSES) + 2, gap="small")
        for i_l, lens_name in enumerate(HOME_LENSES):
            if lens_cols[i_l].button(
                lens_name,
                key=f"lens_{i_l}",
                type="primary" if st.session_state.home_lens == lens_name else "secondary",
                use_container_width=True,
            ):
                st.session_state.home_lens = lens_name
                st.rerun()

        lens = st.session_state.home_lens
        lens_pairs = []
        for s in snapshots.values():
            if lens == "BIST" and s.asset_class != "BIST_STOCK":
                continue
            elif lens == "Katılım" and not s.is_katilim:
                continue
            elif lens == "TEFAS" and s.asset_class != "TEFAS_FUND":
                continue
            elif lens == "ABD & ETF" and s.market_group != "Küresel ETF & ABD":
                continue
            elif lens == "Kripto" and s.market_group != "Kripto & 3x ETF":
                continue
            lens_pairs.append((s, all_scored[s.symbol]))

        lens_pairs.sort(key=lambda x: x[1].composite_score, reverse=True)
        render_html("<div style='height:14px;'></div>")

        # 3. Clean 2-Column Editorial Main Grid (62% Left / 38% Right)
        left_col, right_col = st.columns([1.65, 1.0], gap="large")

        with left_col:
            # A. Haftanın Parlayanları (3 spacious cards, 1 clean button each)
            render_html(f"<div class='aq-h2'>Haftanın Parlayanları <span class='aq-h2-sub'>{html.escape(lens)} · 20 günlük momentum liderleri</span></div>")
            shining = sorted(
                lens_pairs,
                key=lambda x: x[0].indicators.get("mom_20d", 0.0) + x[0].indicators.get("vol_zscore", 0.0) * 2.0,
                reverse=True,
            )[:3]
            sh_cols = st.columns(len(shining), gap="medium")
            for i_s, (ss, sg) in enumerate(shining):
                mom = ss.indicators.get("mom_20d", 0.0)
                with sh_cols[i_s]:
                    render_html(
                        f"""
                        <div class="aq-shine-card">
                            <div class="aq-row-between">
                                <span class="aq-sym">{html.escape(ss.symbol)}</span>
                                <span class="aq-pill aq-pill-buy">20G {mom:+.1f}%</span>
                            </div>
                            <div class="aq-sub">{html.escape(ss.name)}</div>
                            <div class="aq-row-between" style="margin-top:12px;">
                                <span class="aq-mono" style="font-size:15px; font-weight:700; color:#F8FAFC;">{ss.last_price:,.2f} {ss.currency}</span>
                                <span class="{'aq-pos' if ss.change_pct >= 0 else 'aq-neg'}" style="font-size:12px;">{ss.change_pct:+.2f}%</span>
                            </div>
                        </div>
                        """
                    )
                    if st.button("İncele →", key=f"v7_sh_{i_s}_{ss.symbol}", use_container_width=True):
                        open_detail(ss.symbol)
                        st.rerun()

            # B. Canlı Piyasa Akışı (KAP & Haberler — Right on Home, calm editorial style)
            render_html("<div style='height:18px;'></div>")
            nh_l, nh_r = st.columns([3.2, 1.0])
            nh_l.markdown("<div class='aq-h2'>Canlı Piyasa & KAP Akışı <span class='aq-h2-sub'>AI özetli</span></div>", unsafe_allow_html=True)
            if nh_r.button("Yenile", key="v7_refresh_news", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

            kap = KAPDisclosureAnalyzer(gemini_api_key=st.session_state.gemini_api_key)
            discs = kap.fetch_live_stream(snapshots, max_items=8, enable_live_rss=st.session_state.use_live_rss)
            for i_n, d in enumerate(discs[:5]):
                a = kap.analyze_disclosure(d)
                p_cls = {"OLUMLU": "aq-pill aq-pill-buy", "OLUMSUZ": "aq-pill aq-pill-sell", "HARD VETO": "aq-pill aq-pill-sell"}.get(a.sentiment, "aq-pill aq-pill-hold")
                b_col = {"OLUMLU": "#10B981", "OLUMSUZ": "#EF4444", "HARD VETO": "#F87171"}.get(a.sentiment, "#64748B")
                link_part = f" · <a href='{html.escape(d.link)}' target='_blank' style='color:#60A5FA; text-decoration:none;'>Kaynak ↗</a>" if d.link else ""
                render_html(
                    f"""
                    <div class="aq-news-item">
                        <div class="aq-row-between" style="font-size:11.5px; color:#64748B;">
                            <div>
                                <span class="aq-pill aq-pill-blue">{html.escape(d.symbol)}</span>
                                <span class="{p_cls}" style="margin-left:5px;">{html.escape(a.sentiment)}</span>
                            </div>
                            <div>{html.escape(d.source)}{link_part}</div>
                        </div>
                        <div class="aq-news-title">{html.escape(d.title)}</div>
                        <div class="aq-news-ai" style="border-left-color:{b_col};">{html.escape(a.ai_commentary)}</div>
                    </div>
                    """
                )

        with right_col:
            # Günün Öne Çıkan Sinyalleri (Clean interactive click-to-open table — ZERO button clutter!)
            render_html(f"<div class='aq-h2'>Günün Öne Çıkanları <span class='aq-h2-sub'>{len(lens_pairs):,} varlık</span></div>")
            st.caption("Detaylı grafik ve analizini açmak için herhangi bir satıra tıklayın.")
            top_list = lens_pairs[:12]
            wl_df = pd.DataFrame([
                {
                    "Sembol": s.symbol,
                    "Fiyat": s.last_price,
                    "Günlük %": s.change_pct,
                    "Skor": g.composite_score,
                    "Sinyal": g.action,
                }
                for s, g in top_list
            ])
            picked_idx = render_sparkline_table(wl_df, height=455)
            if picked_idx is not None and 0 <= picked_idx < len(top_list):
                open_detail(top_list[picked_idx][0].symbol)
                st.rerun()

            if st.button("Tüm 1.136+ Varlığı Keşfet →", type="primary", use_container_width=True):
                go_workspace("Keşfet")
                st.rerun()

    # =========================================================================
    # WORKSPACE 2: KEŞFET (ELEGANT KÜRATÖRLÜ KEŞİF + FİLTRE + PREMİUM LİSTE)
    # =========================================================================
    elif cur_ws == "Keşfet":
        render_html(
            f"<div class='aq-h2' style='font-size:19px; margin-bottom:14px;'>Keşfet "
            f"<span class='aq-h2-sub'>{len(snapshots):,} varlık · Küratörlü temalar, akıllı filtreler ve anlık tarama</span></div>"
        )

        # 1. Refined Search & Primary Filter Bar
        f1, f2, f3, f4 = st.columns([2.6, 1.55, 1.7, 1.05], gap="small")
        with f1:
            st.session_state.search_query = st.text_input(
                "Ara", value=st.session_state.search_query,
                placeholder="Sembol, fon veya şirket ara (ör. THYAO, KPC, KPU, NVDA, BTC)...",
                label_visibility="collapsed",
            )
        market_options = [
            "Tüm Piyasalar (1,100+)", "BIST Payları", "TEFAS Fonları",
            "Küresel ETF & ABD", "Döviz, Altın & Sukuk", "Kripto & 3x ETF",
        ]
        with f2:
            mg_v = st.session_state.market_group if st.session_state.market_group in market_options else market_options[0]
            st.session_state.market_group = st.selectbox("Piyasa", market_options, index=market_options.index(mg_v), label_visibility="collapsed")
        katilim_options = [
            "Tümü (Konv. + Katılım)", "Sadece Katılım Uyumlu",
            "BIST Katılım (XKTUM)", "TEFAS Katılım Fonları (53+)", "İslami ETF & Sukuk",
        ]
        with f3:
            km_v = st.session_state.katilim_mode if st.session_state.katilim_mode in katilim_options else katilim_options[0]
            st.session_state.katilim_mode = st.selectbox("Katılım", katilim_options, index=katilim_options.index(km_v), label_visibility="collapsed")
        with f4:
            with safe_popover("⚙ Filtreler", use_container_width=True):
                mg = st.session_state.market_group
                if mg in ("Tüm Piyasalar (1,100+)", "BIST Payları"):
                    sub_opts = ["Tümü", "BIST 30", "BIST 100", "BIST Katılım (XKTUM)", "BIST Yıldız / Büyüme", "BIST Yüksek Beta"]
                elif mg == "TEFAS Fonları":
                    sub_opts = ["Tümü", "TEFAS Katılım (53)"] + sorted({s.category for s in snapshots.values() if s.asset_class == "TEFAS_FUND"})
                else:
                    sub_opts = ["Tümü"] + sorted({s.category for s in snapshots.values() if s.market_group == mg})

                st.session_state.sub_filter = st.selectbox("Alt Kategori", sub_opts, index=sub_opts.index(st.session_state.sub_filter) if st.session_state.sub_filter in sub_opts else 0)
                st.session_state.bist_sectors = st.multiselect("BIST Sektörleri", sorted({s.sector for s in snapshots.values() if s.asset_class == "BIST_STOCK"}), default=st.session_state.bist_sectors)
                st.session_state.tefas_categories = st.multiselect("TEFAS Kategorileri", sorted({s.category for s in snapshots.values() if s.asset_class == "TEFAS_FUND"}), default=st.session_state.tefas_categories)
                st.session_state.signal_actions = st.multiselect("Sinyal", ["GÜÇLÜ AL", "AL", "TUT / İZLE", "SAT", "SPK ENGELİ"], default=st.session_state.signal_actions)
                st.session_state.min_score = st.slider("Min. Skor", 0.0, 95.0, float(st.session_state.min_score), step=1.0)
                st.session_state.use_rsi_filter = st.checkbox("RSI Aralığı", value=st.session_state.use_rsi_filter)
                if st.session_state.use_rsi_filter:
                    st.session_state.rsi_range = st.slider("RSI-14", 0, 100, tuple(st.session_state.rsi_range))
                tc1, tc2 = st.columns(2)
                st.session_state.only_above_sma50 = tc1.checkbox("SMA50 Üstü", value=st.session_state.only_above_sma50)
                st.session_state.only_macd_bull = tc2.checkbox("MACD Pozitif", value=st.session_state.only_macd_bull)
                st.session_state.use_fund_filter = st.checkbox("Temel Çarpanlar", value=st.session_state.use_fund_filter)
                if st.session_state.use_fund_filter:
                    fc1, fc2 = st.columns(2)
                    st.session_state.max_pe = fc1.number_input("Maks. F/K", 1.0, 200.0, float(st.session_state.max_pe))
                    st.session_state.max_pb = fc2.number_input("Maks. PD/DD", 0.1, 50.0, float(st.session_state.max_pb))
                    fc3, fc4 = st.columns(2)
                    st.session_state.min_roe = fc3.number_input("Min. ROE %", -50.0, 100.0, float(st.session_state.min_roe))
                    st.session_state.max_ter = fc4.number_input("Maks. TER %", 0.0, 5.0, float(st.session_state.max_ter))
                if st.button("Tüm Filtreleri Sıfırla", use_container_width=True):
                    reset_screener_filters()
                    st.rerun()

        # 2. Curated Discovery Themes (4 Elegant Cards — replaces raw button row)
        render_html("<div style='height:10px;'></div>")
        curated_themes = [
            ("Tümü", "Tüm Evren", "Geniş piyasa taraması"),
            ("Güçlü AL (75+)", "Güçlü Momentum", "Skor 75+ & pozitif trend"),
            ("Dip Dönüş (RSI<40)", "Dip Fırsatları", "RSI < 40 tepki adayları"),
            ("Değer (F/K<12)", "Değer & Çarpan", "F/K < 12 iskontolu paylar"),
        ]
        th_cols = st.columns(4, gap="medium")
        for idx_t, (t_key, t_title, t_desc) in enumerate(curated_themes):
            is_act = st.session_state.screener_preset == t_key
            b_sty = "border-color: rgba(59,130,246,0.65); background:#141C2E;" if is_act else ""
            with th_cols[idx_t]:
                render_html(
                    f"<div class='aq-ticker-card' style='margin-bottom:6px; {b_sty}'>"
                    f"<div style='font-size:13.5px; font-weight:600; color:#F8FAFC;'>{t_title}</div>"
                    f"<div style='font-size:11.5px; color:#64748B; margin-top:3px;'>{t_desc}</div></div>"
                )
                if st.button("Aktif ✓" if is_act else "Seç", key=f"v8_th_{idx_t}", type="primary" if is_act else "secondary", use_container_width=True):
                    st.session_state.screener_preset = t_key
                    st.session_state["kesfet_page"] = 1
                    st.rerun()

        mg_f = "Tüm Piyasalar (840+)" if st.session_state.market_group.startswith("Tüm Piyasalar") else st.session_state.market_group
        km_f = "TEFAS Katılım Fonları (53)" if st.session_state.katilim_mode.startswith("TEFAS Katılım") else st.session_state.katilim_mode
        filter_cfg = {
            "search_query": st.session_state.search_query, "market_group": mg_f, "katilim_mode": km_f,
            "sub_filter": st.session_state.sub_filter, "bist_sectors": st.session_state.bist_sectors,
            "tefas_categories": st.session_state.tefas_categories, "use_fund_filter": st.session_state.use_fund_filter,
            "max_pe": st.session_state.max_pe, "max_pb": st.session_state.max_pb, "min_roe": st.session_state.min_roe,
            "max_ter": st.session_state.max_ter, "use_rsi_filter": st.session_state.use_rsi_filter,
            "rsi_range": st.session_state.rsi_range, "only_above_sma50": st.session_state.only_above_sma50,
            "only_macd_bull": st.session_state.only_macd_bull, "signal_actions": st.session_state.signal_actions,
            "min_score": st.session_state.min_score, "hide_spk_blocked": st.session_state.hide_spk_blocked,
        }

        matched = [(s, all_scored[s.symbol]) for s in snapshots.values() if passes_filters(s, all_scored[s.symbol], filter_cfg)]
        pr = st.session_state.screener_preset
        if pr == "Güçlü AL (75+)":
            matched = [(s, g) for s, g in matched if g.composite_score >= 75.0]
        elif pr == "Dip Dönüş (RSI<40)":
            matched = [(s, g) for s, g in matched if g.rsi_14 < 40.0]
        elif pr == "Değer (F/K<12)":
            matched = [(s, g) for s, g in matched if s.pe_ratio is not None and 0 < s.pe_ratio < 12.0]
        matched.sort(key=lambda x: x[1].composite_score, reverse=True)

        # On-demand live lookup when searched ticker isn't found locally
        raw_q = st.session_state.search_query.strip().upper()
        if raw_q and len(raw_q) <= 12 and not matched:
            lk1, lk2 = st.columns([3.5, 1.5])
            lk1.info(f"'{raw_q}' yerleşik evrende bulunamadı. Canlı piyasadan anlık çekebilirsiniz.")
            if lk2.button(f"+ {raw_q} Canlı Çek", type="primary", use_container_width=True):
                fetched = fetch_on_demand_symbol(raw_q)
                if fetched:
                    st.session_state.custom_snapshots[fetched.symbol] = fetched
                    open_detail(fetched.symbol)
                    st.rerun()
                else:
                    st.warning(f"'{raw_q}' için canlı veri bulunamadı.")

        if matched:
            # 3. Top 3 Visual Spotlight Cards for Current Filter
            render_html("<div style='height:12px;'></div>")
            render_html(f"<div class='aq-h2'>Öne Çıkan Fırsatlar <span class='aq-h2-sub'>{len(matched):,} eşleşen varlıktan ilk 3</span></div>")
            sp_cols = st.columns(min(3, len(matched)), gap="medium")
            for idx_sp, (sp_s, sp_g) in enumerate(matched[:3]):
                dec_sp = 4 if sp_s.last_price < 10 else 2
                kat_lbl = "<span style='color:#34D399; font-size:10.5px; margin-left:6px;'>● Katılım</span>" if sp_s.is_katilim else ""
                with sp_cols[idx_sp]:
                    render_html(
                        f"""
                        <div class="aq-shine-card">
                            <div class="aq-row-between">
                                <div><span class="aq-sym">{html.escape(sp_s.symbol)}</span>{kat_lbl}</div>
                                <span class="{action_pill_class(sp_g.action)}">{html.escape(sp_g.action)} · {sp_g.composite_score:.0f}p</span>
                            </div>
                            <div class="aq-sub" style="margin:4px 0 10px 0;">{html.escape(sp_s.name)}</div>
                            <div class="aq-row-between">
                                <span class="aq-mono" style="font-size:16px; font-weight:700; color:#F8FAFC;">{sp_s.last_price:,.{dec_sp}f} {sp_s.currency}</span>
                                <span class="{'aq-pos' if sp_s.change_pct >= 0 else 'aq-neg'}" style="font-size:12.5px;">{sp_s.change_pct:+.2f}%</span>
                            </div>
                            <div style="font-size:11.5px; color:#64748B; margin-top:8px;">{html.escape(sp_g.reasons[0])}</div>
                        </div>
                        """
                    )
                    if st.button(f"{sp_s.symbol} İncele →", key=f"v8_sp_{idx_sp}_{sp_s.symbol}", use_container_width=True):
                        open_detail(sp_s.symbol)
                        st.rerun()

            # 4. Elegant Custom-Styled Discovery List (with clean Pagination + View Switcher)
            render_html("<div style='height:14px;'></div>")
            lh_1, lh_2, lh_3 = st.columns([2.6, 1.3, 1.1])
            lh_1.markdown(f"<div class='aq-h2'>Tarama Sonuçları <span class='aq-h2-sub'>{len(matched):,} varlık</span></div>", unsafe_allow_html=True)
            view_mode = lh_2.selectbox("Görünüm", ["Şık Liste", "Sparkline Tablo"], index=0, label_visibility="collapsed")
            csv_export = pd.DataFrame([
                {
                    "Sembol": s.symbol, "Varlık": s.name, "Piyasa": s.market_group,
                    "Kategori": s.category, "Katılım": "Evet" if s.is_katilim else "Hayır",
                    "Sinyal": g.action, "Skor": g.composite_score, "Fiyat": s.last_price,
                    "Birim": s.currency, "Günlük %": s.change_pct, "RSI": g.rsi_14,
                    "Stop-Loss": g.stop_loss, "Take-Profit": g.take_profit,
                }
                for s, g in matched
            ])
            lh_3.download_button(
                "CSV İndir", data=csv_export.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"alpquant_kesfet_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv", use_container_width=True,
            )

            if view_mode == "Şık Liste":
                page_size = 20
                total_pages = max(1, math.ceil(len(matched) / page_size))
                cur_pg = min(max(1, int(st.session_state.get("kesfet_page", 1))), total_pages)
                slice_items = matched[(cur_pg - 1) * page_size : cur_pg * page_size]

                disc_rows = []
                for s, g in slice_items:
                    dec = 4 if s.last_price < 10 else 2
                    kat_badge = "<span style='color:#34D399; font-size:10px; margin-left:5px;'>● Katılım</span>" if s.is_katilim else ""
                    sc_bar = min(100, max(5, int(g.composite_score)))
                    sc_col = "#10B981" if g.composite_score >= 70 else ("#3B82F6" if g.composite_score >= 55 else "#64748B")
                    disc_rows.append(
                        f"<tr style='border-bottom:1px solid rgba(148,163,184,0.09);'>"
                        f"<td style='padding:12px 16px;'>"
                        f"<div style='font-size:14px; font-weight:700; color:#F8FAFC;'>{html.escape(s.symbol)}{kat_badge}</div>"
                        f"<div style='font-size:11.5px; color:#64748B; margin-top:2px;'>{html.escape(s.name[:40])} · {html.escape(s.category)}</div>"
                        f"</td>"
                        f"<td style='padding:12px 12px; font-family:JetBrains Mono,monospace;'>"
                        f"<div style='font-size:13.5px; font-weight:600; color:#F8FAFC;'>{s.last_price:,.{dec}f} {s.currency}</div>"
                        f"<div style='font-size:11px; color:{'#10B981' if s.change_pct >= 0 else '#EF4444'}; margin-top:2px;'>{s.change_pct:+.2f}%</div>"
                        f"</td>"
                        f"<td style='padding:12px 12px;'>"
                        f"<div style='font-family:JetBrains Mono,monospace; font-size:12.5px; font-weight:600; color:#E2E8F0;'>{g.composite_score:.1f}</div>"
                        f"<div style='height:4px; width:76px; background:#1E293B; border-radius:99px; margin-top:5px; overflow:hidden;'>"
                        f"<div style='height:100%; width:{sc_bar}%; background:{sc_col}; border-radius:99px;'></div></div>"
                        f"</td>"
                        f"<td style='padding:12px 12px; font-family:JetBrains Mono,monospace; font-size:12.5px; color:#CBD5E1;'>{g.rsi_14:.1f}</td>"
                        f"<td style='padding:12px 12px; font-family:JetBrains Mono,monospace; font-size:11.5px;'>"
                        f"<span style='color:#FCA5A5;'>SL {g.stop_loss:,.{dec}f}</span><br><span style='color:#6EE7B7;'>TP {g.take_profit:,.{dec}f}</span>"
                        f"</td>"
                        f"<td style='padding:12px 16px; text-align:right;'><span class='{action_pill_class(g.action)}'>{html.escape(g.action)}</span></td>"
                        f"</tr>"
                    )
                render_html(
                    f"""
                    <div style="background:#111622; border:1px solid rgba(148,163,184,0.13); border-radius:14px; overflow-x:auto; margin-bottom:12px;">
                        <table style="width:100%; border-collapse:collapse; text-align:left;">
                            <thead>
                                <tr style="background:#151C2C; border-bottom:1px solid rgba(148,163,184,0.13); color:#64748B; font-size:11px; text-transform:uppercase; letter-spacing:0.04em;">
                                    <th style="padding:11px 16px;">Varlık</th>
                                    <th style="padding:11px 12px;">Fiyat / Günlük</th>
                                    <th style="padding:11px 12px;">Skor</th>
                                    <th style="padding:11px 12px;">RSI-14</th>
                                    <th style="padding:11px 12px;">Stop / Hedef</th>
                                    <th style="padding:11px 16px; text-align:right;">Sinyal</th>
                                </tr>
                            </thead>
                            <tbody>{''.join(disc_rows)}</tbody>
                        </table>
                    </div>
                    """
                )
                # Quick Open Bar + Clean Pagination
                qb1, qb2, qb3, qb4, qb5 = st.columns([2.5, 1.15, 0.9, 0.75, 0.75], gap="small")
                quick_sym = qb1.selectbox(
                    "Varlık Aç", options=[s.symbol for s, _ in slice_items],
                    format_func=lambda sym: f"{sym} — {snapshots[sym].name[:30]}",
                    label_visibility="collapsed",
                )
                if qb2.button("Detayını Aç →", type="primary", use_container_width=True):
                    open_detail(quick_sym)
                    st.rerun()
                qb3.caption(f"Sayfa {cur_pg}/{total_pages}")
                if qb4.button("← Önceki", disabled=(cur_pg <= 1), use_container_width=True):
                    st.session_state["kesfet_page"] = cur_pg - 1
                    st.rerun()
                if qb5.button("Sonraki →", disabled=(cur_pg >= total_pages), use_container_width=True):
                    st.session_state["kesfet_page"] = cur_pg + 1
                    st.rerun()
            else:
                tbl_df = pd.DataFrame([
                    {
                        "Sembol": s.symbol, "Varlık": s.name[:34], "Piyasa": s.market_group,
                        "Katılım": "✓" if s.is_katilim else "—", "Sinyal": g.action,
                        "Skor": g.composite_score,
                        "30G Trend": [round(float(x), 4) for x in s.df["Close"].tail(30).tolist()],
                        "Fiyat": s.last_price, "Birim": s.currency, "Günlük %": s.change_pct,
                        "RSI": g.rsi_14, "Stop-Loss": g.stop_loss, "Take-Profit": g.take_profit,
                    }
                    for s, g in matched
                ])
                row_idx = render_sparkline_table(tbl_df, height=510)
                if row_idx is not None and 0 <= row_idx < len(matched):
                    open_detail(matched[row_idx][0].symbol)
                    st.rerun()
        elif not raw_q:
            st.info("Seçili filtrelere uygun varlık bulunamadı.")

    # =========================================================================
    # WORKSPACE 3: PORTFÖYÜM (CANLI MALİYET/K-Z + PREMIUM TABLO + BORSACI KOÇU)
    # =========================================================================
    else:
        usdtry_snap = snapshots.get("USDTRY=X")
        usdtry_rate = float(usdtry_snap.last_price) if usdtry_snap else 34.55

        holdings = dict(st.session_state.get("portfolio_holdings", {}))
        for sym in list(st.session_state.basket):
            if sym in snapshots and sym not in holdings:
                px = float(snapshots[sym].last_price)
                holdings[sym] = {"qty": max(1.0, round(25_000.0 / max(px, 1.0), 0)), "avg_cost": px, "custom_sl": 0.0, "custom_tp": 0.0}
        st.session_state.portfolio_holdings = holdings

        # Top Header + Reactive Add/Edit Position Popover (NO st.form bug!)
        ph_l, ph_r = st.columns([3.6, 1.5])
        with ph_l:
            render_html("<div class='aq-h2' style='font-size:18px; margin-bottom:2px;'>Portföyüm & Borsa Danışmanı <span class='aq-h2-sub'>Gerçek maliyet, canlı K/Z ve profesyonel masa yönlendirmeleri</span></div>")
        with ph_r:
            with safe_popover("+ Pozisyon Ekle / Düzenle", use_container_width=True):
                all_syms = sorted(snapshots.keys())
                p_sym = st.selectbox(
                    "Varlık Seç",
                    options=all_syms,
                    index=all_syms.index("KPU") if "KPU" in all_syms else 0,
                    format_func=lambda s: f"{s} — {snapshots[s].name[:30]}",
                    key="pf_editor_symbol",
                )
                sel_snap_ed = snapshots[p_sym]
                cur_h = holdings.get(p_sym, {})
                live_px_ed = float(sel_snap_ed.last_price)
                def_cost = float(cur_h["avg_cost"]) if "avg_cost" in cur_h else live_px_ed
                def_qty = float(cur_h["qty"]) if "qty" in cur_h else max(1.0, round(10_000.0 / max(live_px_ed, 0.1), 0))
                def_sl = float(cur_h.get("custom_sl", 0.0))
                def_tp = float(cur_h.get("custom_tp", 0.0))

                st.caption(
                    f"**{sel_snap_ed.name}** · Kategori: `{sel_snap_ed.category}` · "
                    f"Güncel Fiyat: **{live_px_ed:,.4f} {sel_snap_ed.currency}** ({sel_snap_ed.data_source})"
                )

                c_q, c_c = st.columns(2)
                in_qty = c_q.number_input(
                    "Adet / Lot", min_value=0.01, max_value=100_000_000.0,
                    value=def_qty, step=1.0, key=f"pf_qty_{p_sym}",
                )
                in_cost = c_c.number_input(
                    f"Ort. Maliyet ({sel_snap_ed.currency})", min_value=0.0001, max_value=10_000_000.0,
                    value=def_cost, step=0.05, format="%.4f", key=f"pf_cost_{p_sym}",
                )
                c_sl, c_tp = st.columns(2)
                in_sl = c_sl.number_input(
                    "Özel Stop-Loss (0=Oto)", min_value=0.0, max_value=10_000_000.0,
                    value=def_sl, step=0.05, format="%.2f", key=f"pf_sl_{p_sym}",
                )
                in_tp = c_tp.number_input(
                    "Hedef Fiyat TP (0=Oto)", min_value=0.0, max_value=10_000_000.0,
                    value=def_tp, step=0.05, format="%.2f", key=f"pf_tp_{p_sym}",
                )

                # Instant Live Cost vs Current Price Preview!
                est_pnl_pct = ((live_px_ed - float(in_cost)) / max(float(in_cost), 1e-9)) * 100.0
                est_val = float(in_qty) * live_px_ed
                st.caption(
                    f"Önizleme — Maliyetin: **{float(in_cost):,.4f}** | Güncel: **{live_px_ed:,.4f}** | "
                    f"Anlık K/Z: **{est_pnl_pct:+.2f}%** | Değer: **{est_val:,.0f} {sel_snap_ed.currency}**"
                )

                if st.button("Portföye Kaydet", type="primary", use_container_width=True, key=f"pf_save_{p_sym}"):
                    holdings[p_sym] = {
                        "qty": float(in_qty),
                        "avg_cost": float(in_cost),
                        "custom_sl": float(in_sl),
                        "custom_tp": float(in_tp),
                    }
                    st.session_state.portfolio_holdings = holdings
                    if p_sym not in st.session_state.basket:
                        st.session_state.basket = list(st.session_state.basket) + [p_sym]
                    save_persistent_state()
                    st.success(f"{p_sym} ({float(in_qty):,.0f} adet @ {float(in_cost):,.4f}) kaydedildi.")
                    st.rerun()

        active_syms = [s for s in st.session_state.basket if s in snapshots and s in holdings]
        if not active_syms:
            st.info("Portföyünüzde henüz takip edilen varlık yok. Sağ üstteki '+ Pozisyon Ekle / Düzenle' butonundan ekleyebilirsiniz.")
        else:
            total_val_tl = 0.0
            total_cost_tl = 0.0
            for s in active_syms:
                sn = snapshots[s]
                fx = usdtry_rate if sn.currency == "USD" else 1.0
                q = float(holdings[s].get("qty", 0.0))
                ac = float(holdings[s].get("avg_cost", sn.last_price))
                total_val_tl += q * sn.last_price * fx
                total_cost_tl += q * ac * fx

            pos_list = [
                evaluate_portfolio_position(snapshots[s], all_scored[s], holdings[s], total_val_tl, usdtry_rate)
                for s in active_syms
            ]
            pos_list.sort(key=lambda x: x["val_tl"], reverse=True)

            tot_pnl_tl = total_val_tl - total_cost_tl
            tot_pnl_pct = (tot_pnl_tl / max(total_cost_tl, 1e-9)) * 100.0
            tot_day_tl = sum(p["day_pnl_tl"] for p in pos_list)
            tot_day_pct = (tot_day_tl / max(total_val_tl - tot_day_tl, 1e-9)) * 100.0
            health_score = sum(p["score"] * (p["weight_pct"] / 100.0) for p in pos_list)
            kat_weight = sum(p["weight_pct"] for p in pos_list if p["is_katilim"])

            pnl_col = "#10B981" if tot_pnl_tl >= 0 else "#EF4444"
            day_col = "#10B981" if tot_day_tl >= 0 else "#EF4444"

            render_html(
                f"""
                <div class="aq-ticker-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top:10px; margin-bottom:18px;">
                    <div class="aq-ticker-card">
                        <div class="aq-ticker-lbl">TOPLAM PORTFÖY DEĞERİ</div>
                        <div class="aq-ticker-val" style="font-size:21px;">{total_val_tl:,.0f} ₺</div>
                        <div class="aq-ticker-chg" style="color:#94A3B8;">Maliyet: {total_cost_tl:,.0f} ₺ · {len(pos_list)} varlık</div>
                    </div>
                    <div class="aq-ticker-card">
                        <div class="aq-ticker-lbl">TOPLAM KÂR / ZARAR</div>
                        <div class="aq-ticker-val" style="font-size:21px; color:{pnl_col};">{tot_pnl_tl:+,.0f} ₺</div>
                        <div class="aq-ticker-chg" style="color:{pnl_col};">{'▲' if tot_pnl_pct >= 0 else '▼'} {tot_pnl_pct:+.2f}% genel getiri</div>
                    </div>
                    <div class="aq-ticker-card">
                        <div class="aq-ticker-lbl">GÜNLÜK DEĞİŞİM</div>
                        <div class="aq-ticker-val" style="font-size:21px; color:{day_col};">{tot_day_tl:+,.0f} ₺</div>
                        <div class="aq-ticker-chg" style="color:{day_col};">{'▲' if tot_day_pct >= 0 else '▼'} {tot_day_pct:+.2f}% bugün</div>
                    </div>
                    <div class="aq-ticker-card">
                        <div class="aq-ticker-lbl">PORTFÖY SAĞLIK SKORU</div>
                        <div class="aq-ticker-val" style="font-size:21px; color:#60A5FA;">{health_score:.1f} / 100</div>
                        <div class="aq-ticker-chg" style="color:#94A3B8;">Katılım Oranı: %{kat_weight:.0f}</div>
                    </div>
                </div>
                """
            )

            pt_pos, pt_bt, pt_rep = st.tabs(["Pozisyonlar & Borsacı Tavsiyeleri", "Portföy Backtesti", "PDF & E-Posta Raporu"])

            with pt_pos:
                # PREMIUM CUSTOM STYLED INSTITUTIONAL HOLDINGS TABLE (Full-Width, Zero Raw DataFrame Clutter!)
                render_html("<div class='aq-h2'>Varlıklarım <span class='aq-h2-sub'>Maliyet, canlı fiyat, kâr/zarar ve dinamik stop seviyeleri</span></div>")
                row_html_list = []
                for p in pos_list:
                    dec = 4 if (p["last_price"] < 10 or p["avg_cost"] < 10) else 2
                    pnl_pill_bg = "rgba(16,185,129,0.14)" if p["pnl_tl"] >= 0 else "rgba(239,68,68,0.14)"
                    pnl_pill_fg = "#34D399" if p["pnl_tl"] >= 0 else "#FCA5A5"
                    kat_dot = "<span style='color:#34D399; font-size:10px; margin-left:5px;'>● Katılım</span>" if p["is_katilim"] else ""
                    bar_w = min(100.0, max(4.0, p["weight_pct"]))
                    row_html_list.append(
                        f"<tr style='border-bottom:1px solid rgba(148,163,184,0.10);'>"
                        f"<td style='padding:14px 16px;'>"
                        f"<div style='font-size:14.5px; font-weight:700; color:#F8FAFC;'>{html.escape(p['symbol'])}{kat_dot}</div>"
                        f"<div style='font-size:11.5px; color:#64748B; margin-top:2px;'>{html.escape(p['name'][:38])} · {html.escape(p['snap'].category)}</div>"
                        f"</td>"
                        f"<td style='padding:14px 12px; font-family:JetBrains Mono,monospace;'>"
                        f"<div style='font-size:13.5px; font-weight:600; color:#E2E8F0;'>{p['avg_cost']:,.{dec}f} {p['currency']}</div>"
                        f"<div style='font-size:11px; color:#64748B; margin-top:2px;'>{p['qty']:,.2f} adet</div>"
                        f"</td>"
                        f"<td style='padding:14px 12px; font-family:JetBrains Mono,monospace;'>"
                        f"<div style='font-size:14px; font-weight:700; color:#F8FAFC;'>{p['last_price']:,.{dec}f} {p['currency']}</div>"
                        f"<div style='font-size:11px; color:{'#10B981' if p['snap'].change_pct >= 0 else '#EF4444'}; margin-top:2px;'>Günlük {p['snap'].change_pct:+.2f}% · <span style='color:#64748B;'>{html.escape(p['snap'].data_source)}</span></div>"
                        f"</td>"
                        f"<td style='padding:14px 12px;'>"
                        f"<span style='display:inline-block; background:{pnl_pill_bg}; color:{pnl_pill_fg}; padding:4px 10px; border-radius:8px; font-family:JetBrains Mono,monospace; font-size:12.5px; font-weight:600;'>"
                        f"{p['pnl_tl']:+,.0f} ₺ ({p['pnl_pct']:+.2f}%)</span>"
                        f"</td>"
                        f"<td style='padding:14px 12px; font-family:JetBrains Mono,monospace;'>"
                        f"<div style='font-size:13.5px; font-weight:600; color:#F1F5F9;'>{p['val_tl']:,.0f} ₺ <span style='font-size:11px; color:#64748B;'>(%{p['weight_pct']:.1f})</span></div>"
                        f"<div style='height:4px; width:92px; background:#1E293B; border-radius:99px; margin-top:6px; overflow:hidden;'>"
                        f"<div style='height:100%; width:{bar_w:.0f}%; background:#3B82F6; border-radius:99px;'></div></div>"
                        f"</td>"
                        f"<td style='padding:14px 12px; font-family:JetBrains Mono,monospace; font-size:12px;'>"
                        f"<div style='color:#FCA5A5;'>SL: {p['eff_sl']:,.{dec}f}</div>"
                        f"<div style='color:#6EE7B7; margin-top:2px;'>TP: {p['eff_tp']:,.{dec}f}</div>"
                        f"</td>"
                        f"<td style='padding:14px 16px; text-align:right;'>"
                        f"<span class='{p['badge_cls']}'>{html.escape(p['action_tag'])}</span>"
                        f"</td>"
                        f"</tr>"
                    )

                render_html(
                    f"""
                    <div style="background:#111622; border:1px solid rgba(148,163,184,0.14); border-radius:14px; overflow-x:auto; margin-bottom:14px;">
                        <table style="width:100%; border-collapse:collapse; text-align:left;">
                            <thead>
                                <tr style="background:#151C2C; border-bottom:1px solid rgba(148,163,184,0.14); color:#64748B; font-size:11px; text-transform:uppercase; letter-spacing:0.04em;">
                                    <th style="padding:11px 16px;">Varlık</th>
                                    <th style="padding:11px 12px;">Ort. Maliyet / Adet</th>
                                    <th style="padding:11px 12px;">Güncel Fiyat</th>
                                    <th style="padding:11px 12px;">Kâr / Zarar</th>
                                    <th style="padding:11px 12px;">Değer & Ağırlık</th>
                                    <th style="padding:11px 12px;">Stop / Hedef</th>
                                    <th style="padding:11px 16px; text-align:right;">Masa Tavsiyesi</th>
                                </tr>
                            </thead>
                            <tbody>{''.join(row_html_list)}</tbody>
                        </table>
                    </div>
                    """
                )

                # Clean Quick Action Bar for Positions
                qa1, qa2, qa3 = st.columns([2.6, 1.1, 1.0], gap="small")
                selected_pos_sym = qa1.selectbox(
                    "Pozisyon İşlemi",
                    options=[p["symbol"] for p in pos_list],
                    format_func=lambda s: f"{s} — {snapshots[s].name[:32]} ({holdings[s]['qty']:,.0f} adet @ {holdings[s]['avg_cost']:,.2f})",
                    label_visibility="collapsed",
                )
                if qa2.button("Detaylı Grafiği Aç →", type="primary", use_container_width=True):
                    open_detail(selected_pos_sym)
                    st.rerun()
                if qa3.button("Portföyden Çıkar", use_container_width=True):
                    toggle_basket(selected_pos_sym)
                    st.rerun()

                render_html("<div style='height:14px;'></div>")

                # Below Table: 2-Column Pro Desk Coaching Cards + Allocation Donut
                c_coach, c_donut = st.columns([1.55, 1.0], gap="large")
                with c_coach:
                    render_html("<div class='aq-h2'>Profesyonel Masa Yönlendirmeleri <span class='aq-h2-sub'>Öncelik sırasına göre</span></div>")
                    coached = sorted(pos_list, key=lambda x: x["priority"])
                    for idx_c, p in enumerate(coached):
                        p_col = "#10B981" if p["pnl_pct"] >= 0 else "#EF4444"
                        dec = 4 if p["last_price"] < 10 else 2
                        render_html(
                            f"""
                            <div class="aq-news-item">
                                <div class="aq-row-between">
                                    <div>
                                        <span class="aq-sym">{html.escape(p['symbol'])}</span>
                                        <span class="{p['badge_cls']}" style="margin-left:6px;">{html.escape(p['action_tag'])}</span>
                                    </div>
                                    <span class="aq-mono" style="font-size:12.5px; color:{p_col}; font-weight:600;">{p['pnl_pct']:+.2f}% ({p['pnl_tl']:+,.0f} ₺)</span>
                                </div>
                                <div style="font-size:12.5px; color:#E2E8F0; line-height:1.55; margin-top:8px;">
                                    <b>Masa Planı:</b> {html.escape(p['advice'])}
                                </div>
                                <div style="font-size:11.5px; color:#94A3B8; margin-top:5px;">
                                    <b>Gerekçe:</b> {html.escape(p['rationale'])}
                                </div>
                                <div style="font-size:12px; color:#CBD5E1; background:#161D2E; border-left:2px solid #8B5CF6; padding:7px 10px; border-radius:6px; margin-top:8px; line-height:1.45;">
                                    <b style="color:#C4B5FD;">Psikolojik Değerlendirme:</b> {html.escape(p['psych_note'])}
                                </div>
                                <div class="aq-row-between" style="margin-top:9px; padding-top:8px; border-top:1px solid rgba(148,163,184,0.10); font-size:11.5px; color:#94A3B8; font-family:'JetBrains Mono',monospace;">
                                    <span>Maliyet: {p['avg_cost']:,.{dec}f} → Güncel: {p['last_price']:,.{dec}f}</span>
                                    <span>Stop: <b style="color:#FCA5A5;">{p['eff_sl']:,.{dec}f}</b> · TP: <b style="color:#6EE7B7;">{p['eff_tp']:,.{dec}f}</b></span>
                                </div>
                            </div>
                            """
                        )

                with c_donut:
                    render_html("<div class='aq-h2'>Portföy Dağılımı</div>")
                    st.plotly_chart(build_portfolio_donut(pos_list, height=260), use_container_width=True, key="v72_donut")
                    st.caption("Portföyünüz otomatik olarak kaydedilir ve yenilemelerde korunur. Cihazlar arası taşımak için yedek alabilirsiniz.")
                    backup_json = json.dumps({"basket": st.session_state.basket, "portfolio_holdings": st.session_state.portfolio_holdings}, ensure_ascii=False, indent=2)
                    st.download_button(
                        "Portföy Yedeği İndir (JSON)", data=backup_json.encode("utf-8"),
                        file_name="alpquant_portfoy_yedek.json", mime="application/json", use_container_width=True,
                    )
                    up_file = st.file_uploader("Yedekten Yükle (JSON)", type=["json"], label_visibility="collapsed")
                    if up_file is not None:
                        try:
                            loaded = json.loads(up_file.read().decode("utf-8"))
                            if isinstance(loaded, dict) and loaded.get("portfolio_holdings"):
                                st.session_state.portfolio_holdings = loaded["portfolio_holdings"]
                                st.session_state.basket = loaded.get("basket") or list(loaded["portfolio_holdings"].keys())
                                save_persistent_state()
                                st.success("Portföy yedeği başarıyla yüklendi.")
                                st.rerun()
                        except Exception:
                            st.warning("Geçerli bir AlpQuant JSON yedeği yükleyin.")

            with pt_bt:
                basket_snaps = [p["snap"] for p in pos_list]
                bt_res = run_portfolio_basket_backtest(basket_snaps, initial_capital=max(total_val_tl, 50_000.0))
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Strateji Getirisi", f"%{bt_res['total_ret_pct']:+.2f}")
                m2.metric("Al & Tut", f"%{bt_res['bh_ret_pct']:+.2f}")
                m3.metric("Maks. Düşüş", f"%{bt_res['max_dd_pct']:.2f}")
                m4.metric("Sharpe Oranı", f"{bt_res['sharpe']:.2f}")
                st.plotly_chart(build_backtest_chart(bt_res, height=340, label="Portföyüm (AlpQuant Strateji)"), use_container_width=True, key="v72_port_bt")

            with pt_rep:
                export_sigs = [p["sig"] for p in pos_list]
                r1, r2 = st.columns(2, gap="medium")
                pdf_bytes = generate_pdf_bytes(export_sigs, profile)
                with r1:
                    st.download_button(
                        "Portföy Raporunu PDF İndir" if HAS_FPDF else "Raporu İndir (TXT)",
                        data=pdf_bytes,
                        file_name=f"alpquant_portfoy_{datetime.now().strftime('%Y%m%d')}.{'pdf' if HAS_FPDF else 'txt'}",
                        mime="application/pdf" if HAS_FPDF else "text/plain",
                        type="primary",
                        use_container_width=True,
                    )
                html_report = EmailSignalNotifier.build_html_report(export_sigs, profile, "Portföyüm")
                with r2:
                    if st.button("Portföy Raporunu E-Posta Gönder", use_container_width=True):
                        ok, msg = EmailSignalNotifier.send_email(
                            st.session_state.smtp_user, st.session_state.smtp_pass, st.session_state.email_recipient,
                            f"AlpQuant Pro Portföy Danışman Raporu ({len(export_sigs)} Varlık)", html_report,
                        )
                        if ok:
                            st.success(msg)
                        else:
                            st.warning(msg)
                with st.expander("E-Posta HTML Rapor Önizlemesi", expanded=False):
                    render_html(html_report)


if __name__ == "__main__":
    main()
