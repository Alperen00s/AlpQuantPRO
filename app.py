"""
AlpQuant v3.5 — Evrensel Çoklu Varlık Veri Çekici (data_fetcher.py)
===================================================================
Tek bir kuruma bağlı kalmaksızın, SPK Risk Seviyeleri (Seviye 1 - 5) kapsamındaki
TÜM yerel ve küresel varlık sınıflarına erişen birleşik veri katmanı:
  - Seviye 1 (Çok Düşük Risk): Para Piyasası Fonları (TI1, BGP, RPP), TLREF/Mevduat, Hazine Bonosu/Sukuk, Döviz & Gram Altın/Gümüş
  - Seviye 2 (Düşük Risk)    : Eurobond & Borçlanma Araçları Fonları (DBH, AKE, IPV, KSV), ABD Hazine Tahvili ETF'leri (SHY, IEF, TLT)
  - Seviye 3 (Orta Risk)     : BIST 30 Mavi Çip Hisselerin Tamamı (TradingView Canlı Tarama), TEFAS Altın/Değişken Fonlar (TTA, TCD, GMA, KZL), Küresel Endeks ETF'leri (SPY, VOO, URTH, GLD, SPUS)
  - Seviye 4 (Yüksek Risk)   : BIST 100 & 15 Sektör Büyüme Hisselerinin Tamamı (100+ Hisse), TEFAS Hisse Yoğun & Yabancı Teknoloji Fonları (MAC, NNF, IIH, TI2, AFT, YAY, TTE, KPC), Küresel Teknoloji ETF & Hisseleri (QQQ, SMH, NVDA, MSFT, AAPL)
  - Seviye 5 (Çok Yüksek Risk): Kripto Varlıklar (BTC-USD, ETH-USD, SOL-USD, AVAX-USD), Kaldıraçlı/Tematik ETF'ler (TQQQ, SOXL, ARKK), Serbest Fonlar (IPB, KAV), GSYF ve Yüksek Beta (Beta > 1.35) BIST Yan Tahta/Momentum Payları
"""

from __future__ import annotations
import dataclasses
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import math
import os
import re
import smtplib
from typing import Tuple


from dataclasses import dataclass, field
import datetime as dt
from datetime import timezone
from enum import Enum
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

logger = logging.getLogger("AlpQuant.DataFetcher")


class AssetClass(str, Enum):
    MONEY_MARKET_DEPOSIT = "Para_Piyasasi_ve_Mevduat"
    BONDS_EUROBOND_SUKUK = "Tahvil_Eurobond_ve_Sukuk"
    FOREX_PRECIOUS_METALS = "Doviz_Altin_Gumus_Emtia"
    TEFAS_MUTUAL_FUND = "TEFAS_Yatirim_Fonu"
    BIST_BLUECHIP_30 = "BIST_30_Mavi_Cip_Hisse"
    BIST_GROWTH_100 = "BIST_100_Sektor_Hisse"
    BIST_HIGH_BETA = "BIST_Yuksek_Beta_Agresif"
    GLOBAL_ETF_EQUITY = "Kuresel_ETF_ve_ABD_Hisse"
    CRYPTO_LEVERAGED_HEDGE = "Kripto_Kaldiracli_Serbest"


# --- ALPQUANT BIST SEKTÖR VE ENDEKS EVRENİ (100+ HİSSE) ---
BIST_30 = [
    "AKBNK", "ALARK", "ASELS", "ASTOR", "BEKO", "BIMAS", "CCOLA", "DOAS", "EKGYO", "ENJSA",
    "ENKAI", "EREGL", "FROTO", "GARAN", "ISCTR", "KCHOL", "KONTR", "KOZAL", "KRDMD", "MGROS",
    "PGSUS", "SAHOL", "SASA", "SISE", "TCELL", "THYAO", "TOASO", "TTKOM", "TUPRS", "YKBNK",
]

SEKTOR_SOZLUGU: Dict[str, List[str]] = {
    "Banking": ["AKBNK", "GARAN", "YKBNK", "ISCTR", "ALBRK", "VAKBN", "HALKB", "TSKB", "SKBNK"],
    "Holdings & Investments": ["KCHOL", "SAHOL", "AGHOL", "ALARK", "DOHOL", "TKFEN", "ENKAI", "GOHOL"],
    "Automotive": ["FROTO", "TOASO", "DOAS", "TTRAK", "ASUZU", "KARSAN", "TMSN"],
    "Transportation": ["THYAO", "PGSUS", "TAVHL", "CLEBI"],
    "Energy": ["TUPRS", "ENJSA", "GWIND", "GESAN", "SMRTG", "CWENE", "NATEN", "MAGEN", "KARYE", "ZOREN", "AKENR", "ASTOR", "EUPWR", "ALFAS"],
    "Retail": ["BIMAS", "SOKM", "MGROS", "MAVI", "BIZIM", "VAKKO", "TKNSA"],
    "Technology": ["LOGO", "MIATK", "ARDYZ", "VBTYZ", "MOBTL", "PAPIL", "FONET", "KFEIN", "LINK", "ASELS", "KONTR", "SDTTR", "YEOTK"],
    "Food & Agriculture": ["ULKER", "CCOLA", "AEFES", "TATGD", "TUKAS", "YYLGD", "ELITE", "FADEL", "PNLSN", "PETUN"],
    "Mining & Steel": ["EREGL", "KRDMD", "KCAER", "BRSAN", "KOZAL", "CVKMD", "KMPUR", "CEMAS"],
    "Construction": ["CIMSA", "AKCNS", "BUCIM", "OYAKC", "NUHCM", "KLSER", "KAYSE"],
    "Chemicals & Pharma": ["SASA", "PETKM", "AKSA", "DEVA", "TRILC", "GENIL", "RTALB"],
    "Real Estate (REIT)": ["EKGYO", "SRVGY", "ISGYO", "AKFGY", "TSGYO", "PAGYO", "TRGYO", "HLGYO", "VKGYO", "OZKGY", "KZBGY"],
    "Insurance & Brokerage": ["AKGRT", "ANSGR", "TURSG", "ISMEN", "INFO", "OSMEN"],
    "Durables": ["BEKO", "VESBE", "VESTL"],
    "Telecommunications": ["TCELL", "TTKOM"],
}

KATILIM_30 = [
    "BIMAS", "THYAO", "ASELS", "FROTO", "TUPRS", "DOAS", "ENJSA", "ALBRK", "OYAKC", "EREGL",
    "ASTOR", "MIATK", "CWENE", "EUPWR", "ALFAS", "KCAER", "BRSAN", "CIMSA", "BEKO", "LOGO",
    "SOKM", "GWIND", "TTRAK", "YEOTK", "SDTTR", "CVKMD", "KORDS", "KLSER", "GESAN",
]

KATILIM_TUM = sorted(list(set(KATILIM_30 + [
    "KTLEV", "ALTNY", "SRVGY", "LKMNH", "DMLKT", "KAYSE", "FMIZP", "BRYAT", "BRLSM", "BUCIM",
    "TUKAS", "TATGD", "ARDYZ", "MOBTL", "VBTYZ", "BIZIM", "FADEL", "ELITE", "TRILC", "GENIL",
    "DEVA", "VAKFN", "KZBGY", "KLNMA", "INFO", "OSMEN", "TRGYO", "HLGYO", "VKGYO", "KMPUR",
    "SUWEN", "MTRKS", "PCILT", "KARYE", "NATEN", "MAGEN", "ESEN", "AGROT", "REEDR", "EBEBK", "OBAMS",
])))


# --- EVRENSEL ÇOKLU VARLIK KATALOĞU (SEVİYE 1 - 5 TÜM PİYASA ARAÇLARI) ---
UNIVERSAL_MULTI_ASSET_CATALOG: List[Dict[str, Any]] = [
    # =========================================================================
    # SEVİYE 1: ÇOK DÜŞÜK RİSK (Nakit, Para Piyasası Fonları, Mevduat/Sukuk, Döviz & Altın)
    # =========================================================================
    {
        "symbol": "TI1",
        "name": "İş Portföy Para Piyasası (TL) Fonu (TEFAS)",
        "risk_level": 1,
        "asset_class": AssetClass.MONEY_MARKET_DEPOSIT,
        "sector": "TEFAS Money Market",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 6.84,
        "ter": 0.65,
    },
    {
        "symbol": "RPP",
        "name": "Rota Portföy Para Piyasası Fonu (TEFAS)",
        "risk_level": 1,
        "asset_class": AssetClass.MONEY_MARKET_DEPOSIT,
        "sector": "TEFAS Money Market",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 4.92,
        "ter": 0.55,
    },
    {
        "symbol": "KTV",
        "name": "Kısa Vadeli Kira Sertifikası Katılım Fonu (TEFAS)",
        "risk_level": 1,
        "asset_class": AssetClass.MONEY_MARKET_DEPOSIT,
        "sector": "TEFAS Short-Term Sukuk",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 5.42,
        "ter": 1.15,
    },
    {
        "symbol": "TR_HAZINE_BONO",
        "name": "T.C. Hazine Kısa Vadeli İskontolu Bono / Kamu Kira Sertifikası",
        "risk_level": 1,
        "asset_class": AssetClass.BONDS_EUROBOND_SUKUK,
        "sector": "Sovereign Fixed Income",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 104.25,
        "ter": 0.0,
    },
    {
        "symbol": "USDTRY",
        "name": "ABD Doları / Türk Lirası (Spot Kur)",
        "risk_level": 1,
        "asset_class": AssetClass.FOREX_PRECIOUS_METALS,
        "sector": "FX",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 43.20,
        "yf_ticker": "TRY=X",
        "ter": 0.0,
    },
    {
        "symbol": "XAU_TRY",
        "name": "Gram Altın (Spot TL) / Fiziki Altın",
        "risk_level": 1,
        "asset_class": AssetClass.FOREX_PRECIOUS_METALS,
        "sector": "Precious Metals",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 6545.00,
        "yf_ticker": "GC=F",
        "ter": 0.0,
    },
    {
        "symbol": "XAG_TRY",
        "name": "Gram Gümüş (Spot TL) / Fiziki Gümüş",
        "risk_level": 1,
        "asset_class": AssetClass.FOREX_PRECIOUS_METALS,
        "sector": "Precious Metals",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 95.80,
        "yf_ticker": "SI=F",
        "ter": 0.0,
    },

    # =========================================================================
    # SEVİYE 2: DÜŞÜK RİSK (Eurobond, Tahvil/Bono Fonları, ABD Hazine ETF'leri, Özel Sukuk)
    # =========================================================================
    {
        "symbol": "DBH",
        "name": "İş Portföy Eurobond (Döviz) Borçlanma Araçları Fonu (TEFAS)",
        "risk_level": 2,
        "asset_class": AssetClass.BONDS_EUROBOND_SUKUK,
        "sector": "TEFAS Eurobond Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 24.60,
        "ter": 1.20,
    },
    {
        "symbol": "IPV",
        "name": "İş Portföy Kira Sertifikaları (Sukuk) Katılım Fonu (TEFAS)",
        "risk_level": 2,
        "asset_class": AssetClass.BONDS_EUROBOND_SUKUK,
        "sector": "TEFAS Sukuk Fund",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 8.95,
        "ter": 1.10,
    },
    {
        "symbol": "AKE",
        "name": "Ak Portföy Eurobond (Amerikan Doları) Borçlanma Araçları Fonu",
        "risk_level": 2,
        "asset_class": AssetClass.BONDS_EUROBOND_SUKUK,
        "sector": "TEFAS Eurobond Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 19.40,
        "ter": 1.25,
    },
    {
        "symbol": "SHY",
        "name": "iShares 1-3 Year Treasury Bond ETF (ABD Kısa Vadeli Tahvil)",
        "risk_level": 2,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "US Treasury ETF",
        "is_participation": False,
        "currency": "USD",
        "base_price": 82.50,
        "yf_ticker": "SHY",
        "ter": 0.15,
    },
    {
        "symbol": "TLT",
        "name": "iShares 20+ Year Treasury Bond ETF (ABD Uzun Vadeli Tahvil)",
        "risk_level": 2,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "US Treasury ETF",
        "is_participation": False,
        "currency": "USD",
        "base_price": 94.20,
        "yf_ticker": "TLT",
        "ter": 0.15,
    },
    {
        "symbol": "SPSK",
        "name": "SP Funds Dow Jones Global Sukuk ETF (Küresel Sukuk ETF)",
        "risk_level": 2,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "Global Sukuk ETF",
        "is_participation": True,
        "currency": "USD",
        "base_price": 18.10,
        "yf_ticker": "SPSK",
        "ter": 0.55,
    },

    # =========================================================================
    # SEVİYE 3: ORTA RİSK (BIST 30 Mavi Çip Hisseler, Değişken/Altın Fonları, Küresel Endeks ETF)
    # =========================================================================
    {
        "symbol": "THYAO",
        "name": "Türk Hava Yolları (BIST 30 / Katılım 30)",
        "risk_level": 3,
        "asset_class": AssetClass.BIST_BLUECHIP_30,
        "sector": "Transportation",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 295.50,
        "pe": 4.2,
        "pb": 0.85,
        "roe": 24.5,
        "peg": 0.45,
        "beta": 1.05,
        "div_yield": 0.0,
    },
    {
        "symbol": "ASELS",
        "name": "Aselsan Elektronik (BIST 30 / Katılım 30)",
        "risk_level": 3,
        "asset_class": AssetClass.BIST_BLUECHIP_30,
        "sector": "Technology",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 68.40,
        "pe": 14.8,
        "pb": 2.40,
        "roe": 21.0,
        "peg": 0.82,
        "beta": 1.12,
        "div_yield": 0.4,
    },
    {
        "symbol": "TUPRS",
        "name": "Tüpraş Türkiye Petrol Rafinerileri (BIST 30 / Katılım 30)",
        "risk_level": 3,
        "asset_class": AssetClass.BIST_BLUECHIP_30,
        "sector": "Energy",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 164.20,
        "pe": 6.4,
        "pb": 1.35,
        "roe": 28.4,
        "peg": 0.58,
        "beta": 0.96,
        "div_yield": 8.5,
    },
    {
        "symbol": "BIMAS",
        "name": "BİM Birleşik Mağazalar (BIST 30 / Katılım 30)",
        "risk_level": 3,
        "asset_class": AssetClass.BIST_BLUECHIP_30,
        "sector": "Retail",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 512.00,
        "pe": 11.5,
        "pb": 3.10,
        "roe": 31.2,
        "peg": 0.70,
        "beta": 0.85,
        "div_yield": 2.1,
    },
    {
        "symbol": "KCHOL",
        "name": "Koç Holding (BIST 30)",
        "risk_level": 3,
        "asset_class": AssetClass.BIST_BLUECHIP_30,
        "sector": "Holdings & Investments",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 192.40,
        "pe": 5.8,
        "pb": 0.92,
        "roe": 22.0,
        "peg": 0.52,
        "beta": 1.02,
        "div_yield": 3.4,
    },
    {
        "symbol": "GARAN",
        "name": "Garanti BBVA (BIST 30 Bankacılık)",
        "risk_level": 3,
        "asset_class": AssetClass.BIST_BLUECHIP_30,
        "sector": "Banking",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 118.50,
        "pe": 4.5,
        "pb": 1.45,
        "roe": 34.5,
        "peg": 0.40,
        "beta": 1.18,
        "div_yield": 2.8,
    },
    {
        "symbol": "TTA",
        "name": "İş Portföy Altın Fonu (TEFAS Kıymetli Madenler)",
        "risk_level": 3,
        "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
        "sector": "TEFAS Gold Fund",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 15.80,
        "ter": 0.95,
    },
    {
        "symbol": "TCD",
        "name": "Tacirler Portföy Değişken Fon (TEFAS)",
        "risk_level": 3,
        "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
        "sector": "TEFAS Variable Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 42.10,
        "ter": 1.85,
    },
    {
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust (ABD S&P 500 Endeks ETF)",
        "risk_level": 3,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "US Broad Index ETF",
        "is_participation": False,
        "currency": "USD",
        "base_price": 585.00,
        "yf_ticker": "SPY",
        "ter": 0.09,
    },
    {
        "symbol": "SPUS",
        "name": "SP Funds S&P 500 Sharia Industry Exclusions ETF",
        "risk_level": 3,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "Global Islamic Index ETF",
        "is_participation": True,
        "currency": "USD",
        "base_price": 43.20,
        "yf_ticker": "SPUS",
        "ter": 0.45,
    },

    # =========================================================================
    # SEVİYE 4: YÜKSEK RİSK (BIST 100 Sektör/Büyüme Hisseleri, TEFAS Hisse/Teknoloji Fonları, Nasdaq/Teknoloji)
    # =========================================================================
    {
        "symbol": "MIATK",
        "name": "Mia Teknoloji (BIST 100 / Katılım Teknoloji)",
        "risk_level": 4,
        "asset_class": AssetClass.BIST_GROWTH_100,
        "sector": "Technology",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 54.20,
        "pe": 16.2,
        "pb": 4.10,
        "roe": 34.0,
        "peg": 0.90,
        "beta": 1.32,
        "div_yield": 0.0,
    },
    {
        "symbol": "CWENE",
        "name": "CW Enerji Mühendislik (BIST 100 / Katılım Enerji)",
        "risk_level": 4,
        "asset_class": AssetClass.BIST_GROWTH_100,
        "sector": "Energy",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 218.50,
        "pe": 12.4,
        "pb": 2.75,
        "roe": 26.8,
        "peg": 0.78,
        "beta": 1.28,
        "div_yield": 0.0,
    },
    {
        "symbol": "MAVI",
        "name": "Mavi Giyim Sanayi (BIST 100 Perakende)",
        "risk_level": 4,
        "asset_class": AssetClass.BIST_GROWTH_100,
        "sector": "Retail",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 96.40,
        "pe": 9.8,
        "pb": 2.90,
        "roe": 36.5,
        "peg": 0.65,
        "beta": 1.08,
        "div_yield": 3.1,
    },
    {
        "symbol": "MAC",
        "name": "Marmara Capital Portföy Hisse Senedi (TL) Fonu (Hisse Yoğun)",
        "risk_level": 4,
        "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
        "sector": "TEFAS Equity Intensive Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 3.48,
        "ter": 1.78,
    },
    {
        "symbol": "IIH",
        "name": "İstanbul Portföy Üçüncü Hisse Senedi Fonu (Hisse Yoğun)",
        "risk_level": 4,
        "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
        "sector": "TEFAS Equity Intensive Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 26.90,
        "ter": 1.90,
    },
    {
        "symbol": "AFT",
        "name": "Ak Portföy Yeni Teknolojiler Yabancı Hisse Senedi Fonu (TEFAS)",
        "risk_level": 4,
        "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
        "sector": "TEFAS Global Tech Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 0.88,
        "ter": 2.10,
    },
    {
        "symbol": "KPC",
        "name": "Katılım Hisse Senedi Yoğun Fon (TEFAS)",
        "risk_level": 4,
        "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
        "sector": "TEFAS Participation Equity Fund",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 4.82,
        "ter": 1.95,
    },
    {
        "symbol": "QQQ",
        "name": "Invesco QQQ Trust (Nasdaq-100 Teknoloji Endeks ETF)",
        "risk_level": 4,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "US Tech Index ETF",
        "is_participation": False,
        "currency": "USD",
        "base_price": 495.00,
        "yf_ticker": "QQQ",
        "ter": 0.20,
    },
    {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation (ABD Yarı İletken & Yapay Zeka Hisse)",
        "risk_level": 4,
        "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
        "sector": "US Semiconductors",
        "is_participation": True,
        "currency": "USD",
        "base_price": 135.00,
        "yf_ticker": "NVDA",
        "pe": 38.0,
        "pb": 24.0,
        "roe": 68.0,
        "peg": 1.05,
        "beta": 1.65,
    },

    # =========================================================================
    # SEVİYE 5: ÇOK YÜKSEK RİSK (Kripto Varlıklar, Kaldıraçlı ETF'ler, Serbest Fonlar, Yüksek Beta BIST)
    # =========================================================================
    {
        "symbol": "BTC-USD",
        "name": "Bitcoin / USD (Kripto Varlık Spot)",
        "risk_level": 5,
        "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
        "sector": "Cryptocurrency",
        "is_participation": True,
        "currency": "USD",
        "base_price": 84200.00,
        "yf_ticker": "BTC-USD",
        "beta": 2.10,
    },
    {
        "symbol": "ETH-USD",
        "name": "Ethereum / USD (Akıllı Kontrat Kripto Varlık)",
        "risk_level": 5,
        "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
        "sector": "Cryptocurrency",
        "is_participation": True,
        "currency": "USD",
        "base_price": 2980.00,
        "yf_ticker": "ETH-USD",
        "beta": 2.35,
    },
    {
        "symbol": "SOL-USD",
        "name": "Solana / USD (Yüksek Performanslı Katman-1 Kripto)",
        "risk_level": 5,
        "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
        "sector": "Cryptocurrency",
        "is_participation": True,
        "currency": "USD",
        "base_price": 182.00,
        "yf_ticker": "SOL-USD",
        "beta": 2.60,
    },
    {
        "symbol": "TQQQ",
        "name": "ProShares UltraPro QQQ (3x Kaldıraçlı Nasdaq-100 ETF)",
        "risk_level": 5,
        "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
        "sector": "3x Leveraged ETF",
        "is_participation": False,
        "currency": "USD",
        "base_price": 74.50,
        "yf_ticker": "TQQQ",
        "ter": 0.88,
        "beta": 3.00,
    },
    {
        "symbol": "IPB",
        "name": "İstanbul Portföy Birinci Değişken / Serbest Strateji Fonu",
        "risk_level": 5,
        "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
        "sector": "TEFAS Hedge / Dynamic Fund",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 14.20,
        "ter": 2.20,
    },
    {
        "symbol": "KAV",
        "name": "Birinci Serbest Katılım Fonu (Nitelikli Yatırımcı - TEFAS)",
        "risk_level": 5,
        "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
        "sector": "TEFAS Free Participation Fund",
        "is_participation": True,
        "currency": "TRY",
        "base_price": 18.60,
        "ter": 2.50,
    },
    {
        "symbol": "KONTR",
        "name": "Kontrolmatik Teknoloji (Yüksek Beta BIST Teknoloji/Enerji)",
        "risk_level": 5,
        "asset_class": AssetClass.BIST_HIGH_BETA,
        "sector": "Technology",
        "is_participation": False,
        "currency": "TRY",
        "base_price": 48.60,
        "pe": 19.5,
        "pb": 3.80,
        "roe": 25.0,
        "peg": 0.95,
        "beta": 1.68,
    },
]

# Geriye dönük uyumluluk takma adı
KUVEYT_TURK_TEST_ASSETS = UNIVERSAL_MULTI_ASSET_CATALOG


@dataclass
class MarketSnapshot:
    symbol: str
    name: str
    risk_level: int                    # SPK Uygunluk Testi Seviyesi (1 - 5)
    asset_class: AssetClass
    sector: str
    is_participation_compliant: bool
    current_price: float
    currency: str
    ohlcv_history: pd.DataFrame
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe_pct: Optional[float] = None
    peg_ratio: Optional[float] = None
    beta: Optional[float] = None
    div_yield_pct: Optional[float] = None
    expense_ratio_pct: Optional[float] = None
    rsi_tv: Optional[float] = None
    macd_signal_tv: Optional[str] = None
    trend_tv: Optional[str] = None
    timestamp: str = field(default_factory=lambda: dt.datetime.now(timezone.utc).isoformat())


class MultiAssetDataFetcher:
    """
    Seviye 1 - 5 arasındaki TÜM varlık sınıflarını (Para Piyasası, Tahvil/Eurobond/Sukuk,
    Döviz/Altın/Gümüş, TEFAS Fonları, BIST 30/100/Tüm 100+ Hisse, Küresel ETF/ABD Hisseleri
    ve Kripto/Kaldıraçlı/Serbest Fonlar) tek çatı altında çeken evrensel veri motoru.
    """

    def __init__(self, prefer_live_data: bool = True, include_all_bist_from_tv: bool = True) -> None:
        self.prefer_live_data = prefer_live_data
        self.include_all_bist_from_tv = include_all_bist_from_tv
        self._tv_cache: Dict[str, List[Any]] = {}

    def fetch_tradingview_bist_scan(self) -> Dict[str, List[Any]]:
        if self._tv_cache:
            return self._tv_cache
        if not (self.prefer_live_data and HAS_REQUESTS):
            return {}

        url = "https://scanner.tradingview.com/turkey/scan"
        payload = {
            "symbols": {"query": {"types": ["stock"]}},
            "columns": [
                "name", "close", "price_earnings_ttm", "price_book_fq",
                "return_on_equity_ttm", "return_on_equity_fq",
                "dividend_yield_recent", "dividend_yield_forward",
                "price_earnings_growth_ttm", "peg_ratio_ttm",
                "beta_1_year", "RSI", "MACD.macd", "MACD.signal",
                "SMA50", "SMA200", "BB.upper", "BB.lower",
            ],
        }
        try:
            resp = requests.post(url, json=payload, timeout=8)
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    ticker = item["s"].replace("BIST:", "")
                    self._tv_cache[ticker] = item["d"]
        except Exception as exc:
            logger.debug("TradingView taraması çevrimdışı, yedek veri devrede: %s", exc)
        return self._tv_cache

    def generate_synthetic_ohlcv(
        self, symbol: str, base_price: float, days: int = 120, risk_level: int = 3
    ) -> pd.DataFrame:
        seed = sum(ord(c) for c in symbol) % 10000
        rng = np.random.default_rng(seed)
        dates = pd.date_range(end=dt.datetime.now(timezone.utc), periods=days, freq="B")

        vol_map = {1: 0.0020, 2: 0.0045, 3: 0.014, 4: 0.020, 5: 0.032}
        daily_vol = vol_map.get(risk_level, 0.016)
        drift = 0.0013 if risk_level <= 2 else 0.0009

        returns = rng.normal(loc=drift, scale=daily_vol, size=days)
        if symbol in ("THYAO", "TUPRS", "KCHOL", "SPY", "MAC", "IIH", "NVDA", "BTC-USD"):
            returns[-15:-3] = -0.010
            returns[-3:] = [0.016, 0.020, 0.023]
        elif symbol in ("ASELS", "GARAN", "QQQ", "XAU_TRY", "TTA", "ETH-USD"):
            returns[-5:] = [0.009, 0.012, 0.008, 0.015, 0.018]

        price_series = base_price * np.exp(np.cumsum(returns))
        price_series = price_series / price_series[-1] * base_price

        high = price_series * (1.0 + rng.uniform(0.002, daily_vol * 1.3, size=days))
        low = price_series * (1.0 - rng.uniform(0.002, daily_vol * 1.3, size=days))
        open_p = price_series * (1.0 + rng.uniform(-daily_vol * 0.5, daily_vol * 0.5, size=days))
        volume = rng.integers(1_200_000, 15_000_000, size=days).astype(float)
        if symbol in ("THYAO", "ASELS", "TUPRS", "GARAN", "MIATK", "CWENE", "NVDA", "BTC-USD"):
            volume[-2:] *= 2.15

        return pd.DataFrame(
            {
                "Open": np.round(open_p, 4),
                "High": np.round(high, 4),
                "Low": np.round(low, 4),
                "Close": np.round(price_series, 4),
                "Volume": volume,
            },
            index=dates,
        )

    def fetch_snapshot(self, asset_meta: Dict[str, Any], days: int = 120) -> MarketSnapshot:
        symbol = asset_meta["symbol"]
        risk_level = int(asset_meta.get("risk_level", 3))
        base_price = float(asset_meta.get("base_price", 100.0))
        df: Optional[pd.DataFrame] = None

        pe = asset_meta.get("pe")
        pb = asset_meta.get("pb")
        roe = asset_meta.get("roe")
        peg = asset_meta.get("peg")
        beta = asset_meta.get("beta")
        div_yield = asset_meta.get("div_yield")
        rsi_tv = None
        macd_tv = None
        trend_tv = None

        tv_data = self.fetch_tradingview_bist_scan()
        clean_sym = symbol.replace(".IS", "")
        if clean_sym in tv_data:
            d = tv_data[clean_sym]
            if d[1] is not None:
                base_price = float(d[1])
            if d[2] is not None:
                pe = round(float(d[2]), 2)
            if d[3] is not None:
                pb = round(float(d[3]), 2)
            if d[4] is not None or d[5] is not None:
                roe = round(float(d[4] if d[4] is not None else d[5]), 2)
            if d[6] is not None or d[7] is not None:
                div_yield = round(float(d[6] if d[6] is not None else d[7]), 2)
            if d[8] is not None or d[9] is not None:
                peg = round(float(d[8] if d[8] is not None else d[9]), 2)
            if d[10] is not None:
                beta = round(float(d[10]), 2)
            if d[11] is not None:
                rsi_tv = round(float(d[11]), 2)
            if d[12] is not None and d[13] is not None:
                macd_tv = "🟢 Bull" if d[12] > d[13] else "🔴 Bear"
            if d[14] is not None and d[15] is not None:
                trend_tv = "🚀 Up" if d[14] > d[15] else "📉 Down"

        if self.prefer_live_data and HAS_YFINANCE:
            yf_sym = asset_meta.get("yf_ticker")
            if not yf_sym and asset_meta["asset_class"] in (
                AssetClass.BIST_BLUECHIP_30,
                AssetClass.BIST_GROWTH_100,
                AssetClass.BIST_HIGH_BETA,
            ):
                yf_sym = f"{clean_sym}.IS"
            if yf_sym:
                try:
                    hist = yf.Ticker(yf_sym).history(period="6mo")
                    if not hist.empty and len(hist) >= 25:
                        df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
                        if symbol in ("XAU_TRY", "XAG_TRY"):
                            scale = base_price / float(df["Close"].iloc[-1])
                            for col in ("Open", "High", "Low", "Close"):
                                df[col] = np.round(df[col] * scale, 4)
                        base_price = float(df["Close"].iloc[-1])
                except Exception as exc:
                    logger.debug("yfinance geçmiş verisi alınamadı (%s): %s", yf_sym, exc)

        if df is None:
            df = self.generate_synthetic_ohlcv(
                symbol=symbol, base_price=base_price, days=days, risk_level=risk_level
            )

        return MarketSnapshot(
            symbol=symbol,
            name=asset_meta["name"],
            risk_level=risk_level,
            asset_class=asset_meta["asset_class"],
            sector=asset_meta.get("sector", "Other"),
            is_participation_compliant=bool(asset_meta.get("is_participation", False)),
            current_price=float(df["Close"].iloc[-1]),
            currency=asset_meta.get("currency", "TRY"),
            ohlcv_history=df,
            pe_ratio=pe,
            pb_ratio=pb,
            roe_pct=roe,
            peg_ratio=peg,
            beta=beta,
            div_yield_pct=div_yield,
            expense_ratio_pct=asset_meta.get("ter"),
            rsi_tv=rsi_tv,
            macd_signal_tv=macd_tv,
            trend_tv=trend_tv,
        )

    def fetch_all_universe(self, days: int = 120) -> Dict[str, MarketSnapshot]:
        """
        Seviye 1 - 5 Evrensel Varlık Kataloğundaki tüm varlıkları çeker; ayrıca
        TradingView canlı bağlantısı açıksa `SEKTOR_SOZLUGU` içindeki tüm BIST 100
        hisselerini risk seviyelerine (Seviye 3 / 4 / 5) göre dinamik olarak ekler.
        """
        snapshots: Dict[str, MarketSnapshot] = {
            item["symbol"]: self.fetch_snapshot(item, days=days)
            for item in UNIVERSAL_MULTI_ASSET_CATALOG
        }

        tv_data = self.fetch_tradingview_bist_scan()
        if self.include_all_bist_from_tv and tv_data:
            for sector_name, tickers in SEKTOR_SOZLUGU.items():
                for t in tickers:
                    if t in snapshots or t not in tv_data:
                        continue
                    d = tv_data[t]
                    beta_val = float(d[10]) if d[10] is not None else 1.1
                    if t in BIST_30 and beta_val <= 1.30:
                        r_lvl = 3
                        a_cls = AssetClass.BIST_BLUECHIP_30
                    elif beta_val > 1.40:
                        r_lvl = 5
                        a_cls = AssetClass.BIST_HIGH_BETA
                    else:
                        r_lvl = 4
                        a_cls = AssetClass.BIST_GROWTH_100

                    meta = {
                        "symbol": t,
                        "name": f"{t} ({sector_name})",
                        "risk_level": r_lvl,
                        "asset_class": a_cls,
                        "sector": sector_name,
                        "is_participation": t in KATILIM_TUM,
                        "currency": "TRY",
                        "base_price": float(d[1]) if d[1] is not None else 50.0,
                    }
                    snapshots[t] = self.fetch_snapshot(meta, days=days)

        return snapshots






# ============================================================================
# 1. GEMINI AI (`gemini-2.5-flash`) + KURAL TABANLI KAP BİLDİRİM ANALİZÖRÜ
# ============================================================================

def normalize_turkish_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


@dataclass
class KAPDisclosure:
    disclosure_id: str
    symbol: str
    company_name: str
    title: str
    summary_text: str
    published_at: str
    url: str = "https://kap.org.tr/tr/bildirim-sorgu"


@dataclass
class KAPAnalysisResult:
    disclosure_id: str
    symbol: str
    sentiment_label: str
    sentiment_score: float
    confidence: float
    category: str
    is_hard_veto: bool
    extracted_financial_ratio_pct: Optional[float]
    matched_triggers: List[str] = field(default_factory=list)
    gemini_summary: Optional[str] = None
    analysis_engine: str = "Rule-Based NLP"


class KAPDisclosureAnalyzer:
    POSITIVE_PATTERNS: List[Tuple[str, float, str]] = [
        (r"yeni iş ilişkisi|ihale kazan|sözleşme imzalan|sipariş al", 0.45, "Yeni İş İlişkisi / İhale"),
        (r"bedelsiz sermaye artırım|temettü|kar payı dağıtım", 0.35, "Sermaye Artırımı / Temettü"),
        (r"pay geri alım|geri alım programı", 0.30, "Pay Geri Alımı"),
        (r"net dönem karı art|ciro artış|ihracat rekor|favök artış", 0.35, "Güçlü Finansal Sonuç"),
        (r"kapasite artırım|yeni tesis|stratejik ortaklık|teşvik belgesi", 0.30, "Yatırım / Teşvik"),
    ]

    NEGATIVE_PATTERNS: List[Tuple[str, float, str]] = [
        (r"devre kesici|tedbir kararı|brüt takas|kredili işlem yasağı", -0.85, "BIST/SPK Tedbir & Devre Kesici"),
        (r"iflas|konkordato|üretim durdurma|faaliyetlerin durdurulması", -0.95, "Operasyonel Kriz"),
        (r"ihale iptal|sözleşme fesih|sipariş iptali", -0.55, "Sözleşme/İhale İptali"),
        (r"net dönem zararı|zarar açıkladı|vergi cezası|idari para cezası", -0.45, "Finansal Zarar / Ceza"),
        (r"ortak satışı|pay satış bilgi formu", -0.25, "Kurucu/Ortak Pay Satışı"),
    ]

    HARD_VETO_KEYWORDS = re.compile(
        r"devre kesici|tedbir kararı|brüt takas|iflas|konkordato|faaliyetlerin durdurulması",
        re.IGNORECASE,
    )

    REVENUE_RATIO_REGEX = re.compile(
        r"(?:ciro(?:ya|suna)?\s*oranı|gelirlerine\s*oranı|hasılata\s*oranı)[^\d%]*%?\s*(\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    )

    SAMPLE_DISCLOSURES: List[KAPDisclosure] = [
        KAPDisclosure(
            disclosure_id="KAP-2026-1014-01",
            symbol="ASELS",
            company_name="Aselsan Elektronik Sanayi ve Ticaret A.Ş.",
            title="Yeni İş İlişkisi — Savunma Sistemleri İhracat Sözleşmesi",
            summary_text=(
                "Şirketimiz uluslararası bir müşterisi ile savunma sistemleri tedarikine yönelik "
                "yeni iş ilişkisi kapsamında sözleşme imzalamıştır. Sözleşme bedelinin kamuya "
                "açıklanan son yıllık cirosuna oranı %14,2 seviyesindedir."
            ),
            published_at="2026-10-14 09:15",
        ),
        KAPDisclosure(
            disclosure_id="KAP-2026-1014-02",
            symbol="THYAO",
            company_name="Türk Hava Yolları A.O.",
            title="Pay Geri Alım Programı Kapsamında İşlemler ve Yolcu Trafik Artışı",
            summary_text=(
                "Yönetim kurulumuzun pay geri alım programı kapsamında Borsa İstanbul'da pay geri "
                "alımı gerçekleştirilmiş olup dış hat yolcu doluluk oranlarında güçlü artış kaydedilmiştir."
            ),
            published_at="2026-10-14 10:05",
        ),
        KAPDisclosure(
            disclosure_id="KAP-2026-1014-03",
            symbol="TUPRS",
            company_name="Tüpraş Türkiye Petrol Rafinerileri A.Ş.",
            title="Stratejik Dönüşüm Yatırımı, Temettü Dağıtımı ve Kapasite Artırımı",
            summary_text=(
                "Şirketimiz sürdürülebilir havacılık yakıtı ve yeşil hidrojen kapasite artırımı "
                "kapsamında teşvik belgesi almış olup kar payı dağıtım takvimini açıklamıştır."
            ),
            published_at="2026-10-14 10:45",
        ),
        KAPDisclosure(
            disclosure_id="KAP-2026-1014-04",
            symbol="MIATK",
            company_name="Mia Teknoloji A.Ş.",
            title="Yeni İş İlişkisi ve Ar-Ge Teşvik Belgesi Onayı",
            summary_text=(
                "Şirketimiz akıllı ulaşım ve siber güvenlik projeleri kapsamında yeni sözleşme "
                "imzalamış ve teşvik belgesi almıştır. Ciroya oranı %11,5 olarak hesaplanmıştır."
            ),
            published_at="2026-10-14 11:20",
        ),
        KAPDisclosure(
            disclosure_id="KAP-2026-1014-05",
            symbol="CWENE",
            company_name="CW Enerji Mühendislik Ticaret ve Sanayi A.Ş.",
            title="Yeni İş İlişkisi — Güneş Enerjisi Santrali (GES) Anahtar Teslim Sözleşmesi",
            summary_text=(
                "Şirketimiz yurt içinde yerleşik bir müşteri ile GES kurulumu sözleşmesi "
                "imzalamıştır. Sözleşme tutarının son yıllık hasılata oranı %10,8'dir."
            ),
            published_at="2026-10-14 11:45",
        ),
    ]

    def __init__(self, gemini_api_key: Optional[str] = None, model_name: str = "gemini-2.5-flash") -> None:
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name

    def _analyze_rule_based(self, disclosure: KAPDisclosure) -> KAPAnalysisResult:
        full_text = normalize_turkish_lower(f"{disclosure.title} {disclosure.summary_text}")
        score = 0.0
        matched_triggers: List[str] = []
        detected_category = "Genel Bildirim"

        for pattern, weight, cat in self.POSITIVE_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                score += weight
                matched_triggers.append(f"+ {cat} ({weight:+.2f})")
                detected_category = cat

        for pattern, weight, cat in self.NEGATIVE_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                score += weight
                matched_triggers.append(f"- {cat} ({weight:+.2f})")
                detected_category = cat

        extracted_ratio: Optional[float] = None
        ratio_match = self.REVENUE_RATIO_REGEX.search(full_text)
        if ratio_match:
            extracted_ratio = float(ratio_match.group(1).replace(",", "."))
            if extracted_ratio >= 10.0:
                bonus = min(0.35, extracted_ratio / 100.0 * 2.0)
                score += bonus
                matched_triggers.append(f"+ Ciroya Oran Etkisi (%{extracted_ratio:.1f} -> {bonus:+.2f})")

        score = max(-1.0, min(1.0, score))
        is_veto = bool(self.HARD_VETO_KEYWORDS.search(full_text))

        if score >= 0.15:
            label = "OLUMLU"
        elif score <= -0.15:
            label = "OLUMSUZ"
        else:
            label = "NÖTR"

        confidence = min(0.98, 0.55 + abs(score) * 0.45)

        return KAPAnalysisResult(
            disclosure_id=disclosure.disclosure_id,
            symbol=disclosure.symbol.replace(".IS", ""),
            sentiment_label=label,
            sentiment_score=round(score, 3),
            confidence=round(confidence, 2),
            category=detected_category,
            is_hard_veto=is_veto,
            extracted_financial_ratio_pct=extracted_ratio,
            matched_triggers=matched_triggers,
            gemini_summary=None,
            analysis_engine="Rule-Based NLP",
        )

    def _call_gemini_api(self, disclosure: KAPDisclosure) -> Optional[Dict[str, Any]]:
        if not (self.gemini_api_key and HAS_REQUESTS):
            return None
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent?key={self.gemini_api_key}"
        )
        prompt = (
            "Sen Borsa İstanbul (BIST) ve Kamuyu Aydınlatma Platformu (KAP) konusunda uzman "
            "kantitatif bir finansal analistsin. Aşağıdaki KAP bildirimini analiz et ve SADECE "
            "geçerli bir JSON nesnesi döndür:\n"
            f"Hisse Kodu: {disclosure.symbol}\n"
            f"Başlık: {disclosure.title}\n"
            f"İçerik: {disclosure.summary_text}\n\n"
            "JSON şeması:\n"
            "{\n"
            '  "sentiment_label": "OLUMLU" | "OLUMSUZ" | "NÖTR",\n'
            '  "sentiment_score": float (-1.0 ile +1.0 arası),\n'
            '  "confidence": float (0.0 ile 1.0 arası),\n'
            '  "category": string,\n'
            '  "is_hard_veto": bool,\n'
            '  "extracted_financial_ratio_pct": float veya null,\n'
            '  "gemini_summary": string\n'
            "}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        try:
            resp = requests.post(endpoint, json=payload, timeout=12)
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    return json.loads(candidates[0]["content"]["parts"][0]["text"])
        except Exception as exc:
            logger.warning("Gemini API çağrısı başarısız, kural motoru devrede: %s", exc)
        return None

    def analyze_disclosure(self, disclosure: KAPDisclosure) -> KAPAnalysisResult:
        rule_res = self._analyze_rule_based(disclosure)
        gemini_data = self._call_gemini_api(disclosure)
        if not gemini_data:
            return rule_res

        g_score = float(gemini_data.get("sentiment_score", rule_res.sentiment_score))
        blended_score = round(max(-1.0, min(1.0, 0.60 * g_score + 0.40 * rule_res.sentiment_score)), 3)
        is_veto = bool(rule_res.is_hard_veto or gemini_data.get("is_hard_veto", False))
        label = "OLUMLU" if blended_score >= 0.15 else ("OLUMSUZ" if blended_score <= -0.15 else "NÖTR")

        triggers = list(rule_res.matched_triggers)
        if gemini_data.get("gemini_summary"):
            triggers.append(f"Gemini AI: {gemini_data['gemini_summary']}")

        return KAPAnalysisResult(
            disclosure_id=disclosure.disclosure_id,
            symbol=disclosure.symbol.replace(".IS", ""),
            sentiment_label=label,
            sentiment_score=blended_score,
            confidence=round(float(gemini_data.get("confidence", rule_res.confidence)), 2),
            category=str(gemini_data.get("category", rule_res.category)),
            is_hard_veto=is_veto,
            extracted_financial_ratio_pct=(
                gemini_data.get("extracted_financial_ratio_pct")
                if gemini_data.get("extracted_financial_ratio_pct") is not None
                else rule_res.extracted_financial_ratio_pct
            ),
            matched_triggers=triggers,
            gemini_summary=gemini_data.get("gemini_summary"),
            analysis_engine=f"Hybrid (Gemini {self.model_name} + Rule NLP)",
        )


# ============================================================================
# 2. EVRENSEL SPK UYGUNLUK TESTİ MOTORU (Seviye 1 - 5 Tüm Piyasa Varlıkları)
# ============================================================================

@dataclass
class SuitabilityGroupResult:
    level: int
    group_name: str
    is_suitable: bool
    reason_text: str
    recommended_products: List[str]


@dataclass
class SuitabilityProfile:
    user_id: str
    risk_score: int
    approved_levels: List[int]
    restricted_levels: List[int]
    group_results: List[SuitabilityGroupResult]
    behavioral_profile_text: str
    target_level_weights: Dict[int, float]
    participation_only: bool = False


class UniversalSuitabilityEngine:
    """
    Kurumdan bağımsız, SPK Risk Seviyeleri (Seviye 1 - 5) çerçevesinde
    TÜM piyasa varlıklarını (konvansiyonel ve katılım) kapsayan uygunluk motoru.
    """

    GROUP_NAMES = [
        "Çok Düşük Riskli (Seviye 1)",
        "Düşük Riskli (Seviye 2)",
        "Orta Riskli (Seviye 3)",
        "Yüksek Riskli (Seviye 4)",
        "Çok Yüksek Riskli (Seviye 5)",
    ]

    ONERI_VERITABANI: Dict[int, List[str]] = {
        1: [
            "TEFAS Para Piyasası Fonları (TI1, RPP, BGP, KTV)",
            "Vadeli Mevduat / Katılma Hesabı / TLREF Repo",
            "Kısa Vadeli Hazine Bonosu & Kamu Kira Sertifikaları (Sukuk)",
            "Spot Döviz (USD/TRY, EUR/TRY) & Fiziki Gram Altın/Gümüş (XAU, XAG)",
        ],
        2: [
            "TEFAS Eurobond & Borçlanma Araçları Fonları (DBH, AKE, IPV, KSV)",
            "Özel Sektör Tahvilleri & Kira Sertifikaları",
            "ABD Hazine Tahvili & Küresel Sukuk ETF'leri (SHY, IEF, TLT, SPSK)",
        ],
        3: [
            "BIST 30 & Katılım 30 Mavi Çip Hisseleri (THYAO, ASELS, TUPRS, BIMAS, KCHOL, GARAN)",
            "TEFAS Altın, Fon Sepeti & Değişken Fonlar (TTA, TCD, GMA, KZL)",
            "Küresel Ana Endeks ETF'leri (SPY S&P 500, VOO, URTH MSCI World, SPUS)",
        ],
        4: [
            "BIST 100 & 15 Sektördeki Tüm Büyüme Hisseleri (MIATK, CWENE, MAVI, FROTO, PGSUS vb.)",
            "TEFAS Hisse Senedi Yoğun & Yabancı Teknoloji Fonları (MAC, IIH, NNF, AFT, YAY, TTE, KPC)",
            "Küresel Teknoloji ETF'leri & ABD Mega-Cap Hisseler (QQQ Nasdaq-100, SMH, NVDA, MSFT, AAPL)",
        ],
        5: [
            "Kripto Varlıklar Spot (BTC-USD, ETH-USD, SOL-USD, AVAX-USD)",
            "Kaldıraçlı & Tematik Küresel ETF'ler (TQQQ 3x Nasdaq, SOXL 3x Yarı İletken, ARKK)",
            "TEFAS Serbest Fonlar (IPB, KAV), VİOP / Türev Araçlar, GSYF ve Yüksek Beta (Beta > 1.35) BIST Hisseleri (KONTR vb.)",
        ],
    }

    PROFIL_METINLERI: Dict[int, str] = {
        1: "Finansal kayba karşı yüksek hassasiyetiniz var. Anapara koruması, Para Piyasası Fonları (PPF), kısa vadeli Hazine bonosu/sukuk ve spot altın/döviz sizin için en uygun evrendir.",
        2: "Temkinli bir yatırımcısınız. Eurobond fonları, ABD Hazine tahvili ETF'leri (SHY/TLT) ve borçlanma araçlarıyla döviz/faiz bazlı düzenli getiri hedefliyorsunuz.",
        3: "Dengeli bir risk algınız var. BIST 30 mavi çip şirketleri, S&P 500 endeks ETF'leri (SPY/SPUS) ve değişken/altın fonlarıyla orta-uzun vadeli reel büyüme arıyorsunuz.",
        4: "Büyüme odaklısınız. BIST 100 sektör liderleri, TEFAS hisse yoğun/teknoloji fonları (MAC, IIH, AFT) ve Nasdaq/yarı iletken hisselerindeki (QQQ, NVDA) volatiliteyi yönetebilecek kapasitedesiniz.",
        5: "Agresif ve kantitatif bir risksever profilsiniz. Kripto varlıklar (BTC, ETH, SOL), 3x kaldıraçlı ETF'ler (TQQQ), serbest fonlar (IPB, KAV) ve yüksek beta BIST hisseleri dahil tüm piyasa araçlarına erişebilirsiniz.",
    }

    @classmethod
    def evaluate(
        cls,
        user_id: str,
        risk_skoru: int,
        answers: Dict[int, Dict[str, str]],
        participation_only: bool = False,
    ) -> SuitabilityProfile:
        risk_skoru = max(1, min(5, int(risk_skoru)))
        group_results: List[SuitabilityGroupResult] = []
        approved_levels: List[int] = []
        restricted_levels: List[int] = []

        for i, grup_adi in enumerate(cls.GROUP_NAMES, start=1):
            ans = answers.get(
                i,
                {
                    "bilgi": "Yeterince bilgim var",
                    "siklik": "Ayda birkaç kez",
                    "hacim": "50.001 TL - 500.000 TL",
                },
            )
            cevap_bilgi = ans.get("bilgi", "")
            cevap_siklik = ans.get("siklik", "")
            cevap_hacim = ans.get("hacim", "")

            risk_yeterli = risk_skoru >= i
            bilgi_yeterli = True
            tecrube_yeterli = True
            gerekceler: List[str] = []

            if not risk_yeterli:
                gerekceler.append("Risk İştahı Uyumsuz")

            if "Bilgim yok" in cevap_bilgi:
                bilgi_yeterli = False
                gerekceler.append("Teorik Bilgi Eksik")
            elif "Bilgim kısıtlı" in cevap_bilgi and i > 2:
                bilgi_yeterli = False
                gerekceler.append("Teorik Bilgi Yetersiz")

            if i >= 4 and (
                cevap_siklik == "Yılda birkaç kez" or cevap_hacim == "1 TL - 50.000 TL"
            ):
                tecrube_yeterli = False
                gerekceler.append("Pratik Tecrübe Yetersiz")

            is_uygun = risk_yeterli and bilgi_yeterli and tecrube_yeterli
            if is_uygun:
                approved_levels.append(i)
            else:
                restricted_levels.append(i)

            group_results.append(
                SuitabilityGroupResult(
                    level=i,
                    group_name=grup_adi,
                    is_suitable=is_uygun,
                    reason_text="SPK Kriterleri Sağlandı" if is_uygun else " & ".join(gerekceler),
                    recommended_products=cls.ONERI_VERITABANI[i],
                )
            )

        raw_weights = {lvl: float(lvl) for lvl in approved_levels}
        total_w = sum(raw_weights.values()) or 1.0
        target_weights = {
            lvl: round(raw_weights.get(lvl, 0.0) / total_w, 3) for lvl in range(1, 6)
        }

        return SuitabilityProfile(
            user_id=user_id,
            risk_score=risk_skoru,
            approved_levels=approved_levels,
            restricted_levels=restricted_levels,
            group_results=group_results,
            behavioral_profile_text=cls.PROFIL_METINLERI[risk_skoru],
            target_level_weights=target_weights,
            participation_only=participation_only,
        )


# Geriye dönük uyumluluk takma adı
KuveytTurkSuitabilityEngine = UniversalSuitabilityEngine


# ============================================================================
# 3. KANTİTATİF GÖSTERGELER, KARAR MOTORU, BACKTESTER VE E-POSTA SİNYAL BOTU
# ============================================================================

class QuantitativeIndicators:
    @staticmethod
    def compute_metrics(df: pd.DataFrame) -> Dict[str, float]:
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(window=14, min_periods=5).mean()
        loss = (-delta.clip(upper=0.0)).rolling(window=14, min_periods=5).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = float((100.0 - (100.0 / (1.0 + rs))).iloc[-1])
        if math.isnan(rsi):
            rsi = 50.0

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = float((macd_line - signal_line).iloc[-1])

        sma20 = close.rolling(window=20, min_periods=5).mean()
        sma50 = close.rolling(window=50, min_periods=10).mean()
        std20 = close.rolling(window=20, min_periods=5).std()
        upper_bb = sma20 + 2.0 * std20
        lower_bb = sma20 - 2.0 * std20
        last_close = float(close.iloc[-1])
        bb_width_pct = (
            float(((upper_bb.iloc[-1] - lower_bb.iloc[-1]) / max(last_close, 0.01)) * 100.0)
        )

        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = float(tr.rolling(window=14, min_periods=5).mean().iloc[-1])

        low_22d = float(low.tail(22).min())
        dist_from_monthly_low_pct = ((last_close - low_22d) / max(low_22d, 0.01)) * 100.0

        vol_mean = float(volume.tail(20).mean())
        vol_std = float(volume.tail(20).std()) or 1.0
        vol_zscore = (float(volume.iloc[-1]) - vol_mean) / vol_std

        return {
            "rsi_14": round(rsi, 2),
            "macd_hist": round(macd_hist, 4),
            "bb_width_pct": round(bb_width_pct, 2),
            "atr_14": round(atr, 4),
            "dist_from_monthly_low_pct": round(dist_from_monthly_low_pct, 2),
            "volume_zscore": round(vol_zscore, 2),
            "above_sma50": 1.0 if last_close >= float(sma50.iloc[-1]) else 0.0,
        }


class SignalAction(str, Enum):
    STRONG_BUY = "GÜÇLÜ AL"
    BUY = "AL"
    HOLD = "TUT / İZLE"
    SELL = "SAT"
    VETO_KAP = "VETO (KAP TEDBİR/DEVRE KESİCİ)"
    RESTRICTED_SPK = "KISITLI (SPK RİSK SEVİYESİ DIŞI)"
    FILTERED_PARTICIPATION = "FİLTRELİ (KATILIM KRİTERİ DIŞI)"


@dataclass
class DecisionSignal:
    symbol: str
    name: str
    risk_level: int
    asset_class: str
    currency: str
    spk_suitable: bool
    action: SignalAction
    composite_score: float
    technical_score: float
    fundamental_score: float
    kap_sentiment_score: float
    suitability_score: float
    current_price: float
    recommended_quantity: float
    recommended_notional_try: float
    stop_loss_price: float
    take_profit_price: float
    rationale: List[str]
    timestamp: str = field(default_factory=lambda: dt.datetime.now(timezone.utc).isoformat())


class AlpQuantDecisionEngine:
    def __init__(
        self,
        suitability_profile: SuitabilityProfile,
        total_capital_try: float = 250_000.0,
        usd_try_rate: float = 43.20,
    ) -> None:
        self.suitability = suitability_profile
        self.capital_try = total_capital_try
        self.usd_try_rate = usd_try_rate

    def evaluate_asset(
        self,
        snap: MarketSnapshot,
        kap_results: Optional[List[KAPAnalysisResult]] = None,
    ) -> DecisionSignal:
        rationale: List[str] = []
        metrics = QuantitativeIndicators.compute_metrics(snap.ohlcv_history)

        tech_score = 52.0
        rsi = snap.rsi_tv if snap.rsi_tv is not None else metrics["rsi_14"]
        if 30.0 <= rsi <= 48.0:
            tech_score += 18.0
            rationale.append(f"RSI ({rsi:.1f}) aşırı satım/dip dönüş bölgesinde")
        elif 48.0 < rsi <= 65.0:
            tech_score += 12.0
            rationale.append(f"RSI ({rsi:.1f}) sağlıklı pozitif momentumda")
        elif rsi > 75.0:
            tech_score -= 18.0
            rationale.append(f"RSI ({rsi:.1f}) aşırı alım bölgesinde")

        if metrics["macd_hist"] > 0 or snap.macd_signal_tv == "🟢 Bull":
            tech_score += 14.0
            rationale.append("MACD pozitif (Bull) kesişim teyitli")
        else:
            tech_score -= 8.0

        if metrics["dist_from_monthly_low_pct"] <= 7.0 and metrics["macd_hist"] >= 0:
            tech_score += 12.0
            rationale.append(f"1 aylık dibe yakın (+%{metrics['dist_from_monthly_low_pct']}) dönüş formasyonu")

        if metrics["volume_zscore"] >= 1.0:
            tech_score += 8.0
            rationale.append(f"Hacim ortalamanın üzerinde (Z={metrics['volume_zscore']:+.2f})")

        tech_score = max(0.0, min(100.0, tech_score))

        fund_score = 62.0
        if snap.pe_ratio is not None:
            if 0 < snap.pe_ratio <= 10.0:
                fund_score += 18.0
                rationale.append(f"İskontolu F/K çarpanı ({snap.pe_ratio})")
            elif snap.pe_ratio <= 18.0:
                fund_score += 10.0
                rationale.append(f"Makul F/K çarpanı ({snap.pe_ratio})")
            elif snap.pe_ratio > 32.0:
                fund_score -= 10.0
        if snap.roe_pct is not None and snap.roe_pct >= 20.0:
            fund_score += 14.0
            rationale.append(f"Yüksek Özsermaye Kârlılığı (ROE %{snap.roe_pct})")
        if snap.peg_ratio is not None and 0 < snap.peg_ratio <= 1.05:
            fund_score += 10.0
            rationale.append(f"Büyümeye göre cazip PEG ({snap.peg_ratio})")
        if snap.expense_ratio_pct is not None and snap.expense_ratio_pct <= 1.25:
            fund_score += 14.0
            rationale.append(f"Düşük fon yönetim gider kesintisi (TER %{snap.expense_ratio_pct})")

        fund_score = max(0.0, min(100.0, fund_score))

        kap_score = 55.0
        has_kap_veto = False
        if kap_results:
            avg_sent = float(np.mean([r.sentiment_score for r in kap_results]))
            kap_score = max(0.0, min(100.0, 55.0 + avg_sent * 45.0))
            for r in kap_results:
                rationale.append(
                    f"KAP [{r.sentiment_label} {r.sentiment_score:+.2f}]: {r.category} ({', '.join(r.matched_triggers)})"
                )
                if r.is_hard_veto:
                    has_kap_veto = True

        spk_suitable = snap.risk_level in self.suitability.approved_levels
        suitability_score = 90.0 if spk_suitable else 20.0
        if spk_suitable:
            rationale.append(f"SPK Uygunluk Testi Seviye {snap.risk_level} onaylı")
        else:
            rationale.append(f"SPK Risk Kısıtı: Seviye {snap.risk_level} onaylı profil dışında")

        composite = round(
            (0.35 * tech_score)
            + (0.25 * fund_score)
            + (0.25 * kap_score)
            + (0.15 * suitability_score),
            2,
        )

        atr = metrics["atr_14"]
        stop_dist = max(atr * 2.0, snap.current_price * 0.025)
        stop_loss = round(snap.current_price - stop_dist, 4)
        take_profit = round(snap.current_price + (stop_dist * 2.4), 4)

        fx_mult = self.usd_try_rate if snap.currency == "USD" else 1.0
        price_try = snap.current_price * fx_mult
        stop_dist_try = stop_dist * fx_mult

        if self.suitability.participation_only and not snap.is_participation_compliant:
            action = SignalAction.FILTERED_PARTICIPATION
            qty = 0
            notional = 0.0
        elif has_kap_veto:
            action = SignalAction.VETO_KAP
            qty = 0
            notional = 0.0
        elif not spk_suitable:
            action = SignalAction.RESTRICTED_SPK
            qty = 0
            notional = 0.0
        else:
            if composite >= 74.0:
                action = SignalAction.STRONG_BUY
            elif composite >= 63.0:
                action = SignalAction.BUY
            elif composite <= 40.0:
                action = SignalAction.SELL
            else:
                action = SignalAction.HOLD

            if action in (SignalAction.STRONG_BUY, SignalAction.BUY):
                level_weight = self.suitability.target_level_weights.get(snap.risk_level, 0.15)
                max_alloc_try = self.capital_try * min(0.20, max(0.06, level_weight * 0.6))
                risk_budget_try = self.capital_try * 0.015
                qty_by_risk = risk_budget_try / max(stop_dist_try, 0.01)
                qty_by_cap = max_alloc_try / max(price_try, 0.01)
                raw_qty = min(qty_by_risk, qty_by_cap)
                if snap.asset_class in (AssetClass.CRYPTO_LEVERAGED_HEDGE, AssetClass.GLOBAL_ETF_EQUITY) and raw_qty < 1.0:
                    qty = round(max(0.0001, raw_qty), 4)
                else:
                    qty = max(1, int(raw_qty))
                notional = round(qty * price_try, 2)
            else:
                qty = 0
                notional = 0.0

        return DecisionSignal(
            symbol=snap.symbol,
            name=snap.name,
            risk_level=snap.risk_level,
            asset_class=snap.asset_class.value,
            currency=snap.currency,
            spk_suitable=spk_suitable,
            action=action,
            composite_score=composite,
            technical_score=round(tech_score, 2),
            fundamental_score=round(fund_score, 2),
            kap_sentiment_score=round(kap_score, 2),
            suitability_score=suitability_score,
            current_price=snap.current_price,
            recommended_quantity=qty,
            recommended_notional_try=notional,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            rationale=rationale,
        )


@dataclass
class BacktestReport:
    symbol: str
    name: str
    risk_level: int
    total_return_pct: float
    benchmark_buy_hold_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    total_trades: int


class TimeSeriesBacktester:
    def __init__(self, initial_capital: float = 100_000.0, commission_bps: float = 15.0) -> None:
        self.initial_capital = initial_capital
        self.commission_rate = commission_bps / 10_000.0

    def run_backtest(
        self, symbol: str, df: pd.DataFrame, name: str = "", risk_level: int = 3
    ) -> BacktestReport:
        data = df.copy()
        close = data["Close"]
        low = data["Low"]

        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(14, min_periods=5).mean()
        loss = (-delta.clip(upper=0.0)).rolling(14, min_periods=5).mean()
        rs = gain / loss.replace(0, np.nan)
        data["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

        data["sma20"] = close.rolling(20, min_periods=5).mean()
        data["low22"] = low.rolling(22, min_periods=5).min()
        data["dist_low_pct"] = ((close - data["low22"]) / data["low22"]) * 100.0

        position = 0
        entry_price = 0.0
        trades: List[float] = []
        strategy_returns: List[float] = [0.0]

        prices = close.values
        rsis = data["rsi"].values
        sma20s = data["sma20"].values
        dist_lows = data["dist_low_pct"].values

        for i in range(1, len(data)):
            daily_ret = (prices[i] / prices[i - 1]) - 1.0
            if position == 1:
                strategy_returns.append(daily_ret)
                if rsis[i] > 72.0 or prices[i] < sma20s[i] * 0.97:
                    trade_ret = (prices[i] / entry_price) - 1.0 - (2 * self.commission_rate)
                    trades.append(trade_ret)
                    position = 0
            else:
                strategy_returns.append(0.0)
                if (prices[i] >= sma20s[i] and rsis[i] < 65.0) or (
                    dist_lows[i] <= 6.0 and rsis[i] <= 48.0
                ):
                    position = 1
                    entry_price = prices[i]

        if position == 1 and entry_price > 0:
            trades.append((prices[-1] / entry_price) - 1.0 - (2 * self.commission_rate))

        strat_series = pd.Series(strategy_returns, index=data.index)
        equity_curve = (1.0 + strat_series).cumprod()
        total_ret_pct = float((equity_curve.iloc[-1] - 1.0) * 100.0)
        bh_ret_pct = float(((prices[-1] / prices[0]) - 1.0) * 100.0)

        std_ret = float(strat_series.std())
        sharpe = (
            float((strat_series.mean() / std_ret) * np.sqrt(252)) if std_ret > 1e-8 else 0.0
        )

        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_dd_pct = float(drawdown.min() * 100.0)

        wins = [t for t in trades if t > 0]
        win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

        return BacktestReport(
            symbol=symbol,
            name=name or symbol,
            risk_level=risk_level,
            total_return_pct=round(total_ret_pct, 2),
            benchmark_buy_hold_pct=round(bh_ret_pct, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            win_rate_pct=round(win_rate, 1),
            total_trades=len(trades),
        )


class EmailSignalNotifier:
    @staticmethod
    def build_html_report(
        suitability: SuitabilityProfile,
        signals: List[DecisionSignal],
    ) -> str:
        rows_html = ""
        for s in signals:
            badge_color = (
                "#006554"
                if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY)
                else ("#DC3545" if "VETO" in s.action.value or "SAT" in s.action.value else "#6C757D")
            )
            rows_html += f"""
            <tr>
                <td style="padding:8px;border:1px solid #ddd;"><b>{s.symbol}</b><br><small>{s.name}</small></td>
                <td style="padding:8px;border:1px solid #ddd;text-align:center;">Seviye {s.risk_level}</td>
                <td style="padding:8px;border:1px solid #ddd;text-align:center;">
                    <span style="background:{badge_color};color:#fff;padding:3px 8px;border-radius:4px;font-weight:bold;">
                        {s.action.value}
                    </span>
                </td>
                <td style="padding:8px;border:1px solid #ddd;text-align:right;"><b>{s.composite_score:.2f}</b></td>
                <td style="padding:8px;border:1px solid #ddd;text-align:right;">{s.current_price:,.2f} {s.currency}</td>
                <td style="padding:8px;border:1px solid #ddd;text-align:right;">{s.recommended_quantity} Adet<br><small>({s.recommended_notional_try:,.0f} TL)</small></td>
                <td style="padding:8px;border:1px solid #ddd;text-align:right;">SL: {s.stop_loss_price:,.2f}<br>TP: {s.take_profit_price:,.2f}</td>
                <td style="padding:8px;border:1px solid #ddd;font-size:12px;">{"<br>• ".join([""] + s.rationale[:3])[4:]}</td>
            </tr>
            """

        return f"""
        <html>
        <body style="font-family:Segoe UI,Arial,sans-serif;color:#222;">
            <div style="border-top:5px solid #2962FF;padding:16px;background:#F4F7F6;">
                <h2 style="color:#131722;margin:0;">AlpQuant v3.5 — Evrensel Çoklu Varlık Karar Motoru Sinyal Raporu</h2>
                <p style="margin:6px 0 0 0;font-size:14px;">
                    <b>Yatırımcı SPK Risk Skoru:</b> {suitability.risk_score}/5 |
                    <b>Onaylı Seviyeler:</b> {suitability.approved_levels} |
                    <b>Evren Modu:</b> {"Sadece Katılım" if suitability.participation_only else "Tüm Piyasa (Konvansiyonel + Katılım)"} |
                    <b>Tarih:</b> {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
                </p>
            </div>
            <table style="width:100%;border-collapse:collapse;margin-top:14px;font-size:13px;">
                <thead>
                    <tr style="background:#131722;color:#fff;">
                        <th style="padding:8px;border:1px solid #ddd;">Varlık</th>
                        <th style="padding:8px;border:1px solid #ddd;">SPK Risk</th>
                        <th style="padding:8px;border:1px solid #ddd;">Sinyal</th>
                        <th style="padding:8px;border:1px solid #ddd;">Skor</th>
                        <th style="padding:8px;border:1px solid #ddd;">Fiyat</th>
                        <th style="padding:8px;border:1px solid #ddd;">Önerilen Emir</th>
                        <th style="padding:8px;border:1px solid #ddd;">SL / TP</th>
                        <th style="padding:8px;border:1px solid #ddd;">Analitik Gerekçe</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </body>
        </html>
        """

    @classmethod
    def send_email_alert(
        cls,
        recipient_email: str,
        suitability: SuitabilityProfile,
        signals: List[DecisionSignal],
        sender_email: Optional[str] = None,
        smtp_app_password: Optional[str] = None,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
    ) -> Tuple[bool, str]:
        html_body = cls.build_html_report(suitability, signals)
        if not (sender_email and smtp_app_password):
            return (
                False,
                "SMTP Gönderici E-posta / Uygulama Şifresi girilmediği için rapor önizleme modunda hazırlandı.",
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📊 AlpQuant Çoklu Varlık Karar Sinyalleri ({dt.datetime.now().strftime('%d.%m.%Y %H:%M')})"
        msg["From"] = sender_email
        msg["To"] = recipient_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(sender_email, smtp_app_password)
                server.sendmail(sender_email, [recipient_email], msg.as_string())
            return True, f"Sinyal raporu {recipient_email} adresine başarıyla gönderildi."
        except Exception as exc:
            return False, f"SMTP gönderim hatası: {exc}"



# ============================================================================
# 6. STREAMLIT EVRENSEL ÇOKLU VARLIK SÜPER-TERMİNALİ (`app.py`)
# ============================================================================

import tempfile
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import streamlit as st

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False

st.set_page_config(
    page_title="AlpQuant v3.5 | Evrensel Çoklu Varlık Karar Motoru & Finansal Terminal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp { background-color: #131722; color: #D1D4DC; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    [data-testid="stSidebar"] { background-color: #1E222D !important; border-right: 1px solid #2A2E39; }
    h1, h2, h3, h4 { color: #FFFFFF !important; font-weight: 600 !important; }
    p, span, div { color: #D1D4DC; }
    [data-testid="stMetricValue"] { color: #FFFFFF !important; font-size: 1.65rem !important; font-weight: 700; }
    [data-testid="stMetricLabel"] { color: #9BA0AE !important; font-size: 0.85rem !important; text-transform: uppercase; letter-spacing: 0.5px; }
    .stButton > button {
        background-color: #2962FF !important; color: #FFFFFF !important;
        border: none !important; border-radius: 6px !important;
        transition: background-color 0.2s ease !important; font-weight: 600 !important;
    }
    .stButton > button:hover { background-color: #1E53E5 !important; }
    hr { border-color: #2A2E39 !important; margin-top: 0.8rem; margin-bottom: 0.8rem; }
    .aq-card {
        background: linear-gradient(135deg, #1E222D 0%, #161C2E 100%);
        border: 1px solid #2A2E39;
        border-left: 5px solid #2962FF;
        border-radius: 10px;
        padding: 18px;
        margin-bottom: 14px;
    }
    .aq-badge-ok {
        background-color: rgba(8, 153, 129, 0.2);
        color: #00E6B8;
        border: 1px solid #089981;
        padding: 4px 12px;
        border-radius: 16px;
        font-weight: 700;
        font-size: 13px;
    }
    .aq-badge-lock {
        background-color: rgba(242, 54, 69, 0.2);
        color: #FF6B78;
        border: 1px solid #F23645;
        padding: 4px 12px;
        border-radius: 16px;
        font-weight: 700;
        font-size: 13px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "step" not in st.session_state:
    st.session_state.step = 1
if "custom_kap_list" not in st.session_state:
    st.session_state.custom_kap_list = list(KAPDisclosureAnalyzer.SAMPLE_DISCLOSURES)

if "suitability_profile" not in st.session_state:
    default_ans = {
        i: {
            "bilgi": "Yeterince bilgim var",
            "siklik": "Ayda birkaç kez",
            "hacim": "50.001 TL - 500.000 TL",
        }
        for i in range(1, 6)
    }
    st.session_state.suitability_profile = UniversalSuitabilityEngine.evaluate(
        user_id="alperen_gulluoglu", risk_skoru=5, answers=default_ans, participation_only=False
    )

meslekler_listesi = [
    "Seçiniz", "Akademisyen", "Aktüer", "Antrenör", "Araştırma Görevlisi", "Asker", "Aşçı", "Avukat",
    "Bankacı", "Berber / Kuaför", "Bilgisayar Mühendisi", "Bilişim Uzmanı", "Borsacı", "Çiftçi",
    "Danışman", "Denetçi", "Diş Hekimi", "Diyetisyen", "Doktor", "Eczacı", "Ekonomist", "Emlakçı",
    "Emekli", "Endüstri Mühendisi", "Esnaf", "Ev Hanımı", "Finansal Analist", "Fizyoterapist",
    "Gazeteci", "Gemi Kaptanı", "Gümrük Müşaviri", "Güvenlik Görevlisi", "Hakim / Savcı", "Hemşire",
    "İktisatçı", "İnşaat Mühendisi", "İnsan Kaynakları Uzmanı", "İşçi", "İşletmeci", "İtfaiyeci",
    "Kasiyer", "Kimyager", "Kurye / Dağıtım Elemanı", "Makine Mühendisi", "Mali Müşavir",
    "Mimar", "Muhasebeci", "Mühendis (Diğer)", "Müfettiş", "Müteahhit", "Mütercim / Tercüman",
    "Öğrenci", "Öğretmen", "Pazarlama Uzmanı", "Pilot", "Polis", "Proje Yöneticisi", "Psikolog",
    "Reklamcı", "Satış Danışmanı", "Sekreter / Asistan", "Sigortacı", "Sporcu", "Şoför", "Tasarımcı",
    "Teknisyen / Tekniker", "Terzi", "Turizmci / Rehber", "Veteriner", "Yazılımcı / Geliştirici",
    "Yönetici / CEO", "Ziraat Mühendisi", "Diğer",
]

def generate_pdf_file(hisse: str, veri_dict: Dict[str, Any]) -> bytes:
    if not HAS_FPDF:
        return b"FPDF library not installed."
    pdf = FPDF()
    pdf.add_page()

    def tr2eng(text: Any) -> str:
        t = (
            str(text)
            .replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s")
            .replace("ö", "o").replace("ç", "c").replace("İ", "I").replace("Ğ", "G")
            .replace("Ü", "U").replace("Ş", "S").replace("Ö", "O").replace("Ç", "C")
        )
        return t.encode("latin-1", "ignore").decode("latin-1")

    pdf.set_font("Arial", "B", 22)
    pdf.set_text_color(41, 98, 255)
    pdf.cell(0, 12, tr2eng("AlpQuant - MULTI-ASSET RESEARCH REPORT"), ln=1, align="C")
    pdf.set_font("Arial", "B", 11)
    pdf.set_text_color(120, 123, 134)
    pdf.cell(
        0,
        8,
        tr2eng(f"Symbol: {hisse}  |  Sector: {veri_dict.get('Sector', '')}  |  Date: {dt.date.today()}"),
        ln=1,
        align="C",
    )
    pdf.line(10, 32, 200, 32)
    pdf.ln(8)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "1. Financial & Technical Analysis Profile", ln=1)
    pdf.ln(2)

    metrics_list = [
        (k, v) for k, v in veri_dict.items() if k not in ["Ticker", "Sector", "SMA50", "SMA200"]
    ]
    for i in range(0, len(metrics_list), 2):
        k1, v1 = metrics_list[i]
        pdf.set_font("Arial", "B", 11)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(35, 6, tr2eng(f"{k1}:"), ln=0)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(55, 6, tr2eng(str(v1)), ln=0)
        if i + 1 < len(metrics_list):
            k2, v2 = metrics_list[i + 1]
            pdf.set_text_color(100, 100, 100)
            pdf.cell(35, 6, tr2eng(f"{k2}:"), ln=0)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(55, 6, tr2eng(str(v2)), ln=1)
        else:
            pdf.ln(6)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        pdf_path = tmp_pdf.name
    pdf.output(pdf_path)
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    try:
        os.remove(pdf_path)
    except OSError:
        pass
    return pdf_bytes

st.sidebar.markdown("<h3 style='text-align: center; color: #FFFFFF;'>⚙️ AlpQuant Komuta Merkezi</h3>", unsafe_allow_html=True)
st.sidebar.markdown("<hr style='margin: 8px 0;'>", unsafe_allow_html=True)

with st.sidebar.expander("🌐 Varlık Evreni & Hızlı Risk Kalibrasyonu", expanded=True):
    portfoy_bakiye = st.number_input(
        "Toplam Portföy Sermayesi (TL)",
        min_value=10_000.0,
        max_value=50_000_000.0,
        value=250_000.0,
        step=10_000.0,
    )
    evren_modu = st.radio(
        "Piyasa Evreni Kapsamı",
        ("🌐 Tüm Piyasa (Konvansiyonel + Katılım)", "☪️ Sadece Katılım Finans Uyumlu"),
        index=0,
    )
    sadece_katilim_filtre = evren_modu.startswith("☪️")

    hizli_risk = st.slider(
        "Hızlı SPK Risk Skoru (1=Çok Düşük, 5=Tüm Evren/Agresif)",
        min_value=1,
        max_value=5,
        value=st.session_state.suitability_profile.risk_score,
    )
    if st.button("⚡ Risk Profilini ve Evreni Güncelle"):
        ans_quick = {
            i: {
                "bilgi": "Yeterince bilgim var",
                "siklik": "Haftada birkaç kez",
                "hacim": "500.001 TL ve üzeri",
            }
            for i in range(1, 6)
        }
        st.session_state.suitability_profile = UniversalSuitabilityEngine.evaluate(
            user_id="alperen_gulluoglu",
            risk_skoru=hizli_risk,
            answers=ans_quick,
            participation_only=sadece_katilim_filtre,
        )
        st.toast(f"Risk Profili Seviye {hizli_risk}/5 ({evren_modu}) olarak güncellendi!", icon="✅")

    st.session_state.suitability_profile.participation_only = sadece_katilim_filtre
    canli_veri_aktif = st.checkbox("Canlı Veri Akışı (TradingView / yfinance)", value=True)
    tum_bist_tara = st.checkbox("TradingView BIST 100 Tüm Hisseleri Dinamik Ekle", value=True)

with st.sidebar.expander("🧠 Gemini AI (KAP Analiz Motoru)", expanded=False):
    gemini_api_key = st.text_input(
        "Google Gemini API Key",
        value=os.environ.get("GEMINI_API_KEY", ""),
        type="password",
        help="Girildiğinde KAP bildirimleri gemini-2.5-flash modeli ve kural motoru ile hibrit analiz edilir.",
    )
    gemini_model = st.selectbox("Gemini Modeli", ["gemini-2.5-flash", "gemini-2.5-pro"], index=0)

with st.sidebar.expander("📧 E-Posta Sinyal Bildirim Hattı", expanded=False):
    alici_eposta = st.text_input("Alıcı E-Posta Adresi", value="alperengulluoglu@gmail.com")
    gonderici_eposta = st.text_input("Gönderici Gmail Adresi", value="alperengulluoglu@gmail.com")
    smtp_sifre = st.text_input(
        "Gmail Uygulama Şifresi (App Password)",
        value=os.environ.get("SMTP_APP_PASSWORD", ""),
        type="password",
    )

prof: SuitabilityProfile = st.session_state.suitability_profile
st.markdown(
    "<h2 style='margin-bottom: 2px;'>AlpQuant v3.5 <span style='color: #2962FF; font-size: 18px; font-weight: 500;'>| Evrensel Çoklu Varlık Karar Motoru, KAP AI & Otomatik İşlem Botu</span></h2>",
    unsafe_allow_html=True,
)
st.caption(
    f"Aktif SPK Risk Skoru: **{prof.risk_score} / 5**  |  "
    f"Onaylı Varlık Seviyeleri: **{prof.approved_levels}**  |  "
    f"Evren: **{'Sadece Katılım' if prof.participation_only else 'Tüm Piyasa (BIST, TEFAS, Küresel ETF/Hisse, Eurobond, Altın/Döviz, Kripto)'}**  |  "
    f"Sermaye: **{portfoy_bakiye:,.0f} TL**"
)
st.markdown("<hr style='margin-top: 4px;'>", unsafe_allow_html=True)

tab_market, tab_karar, tab_test, tab_varliklar, tab_kap, tab_screener = st.tabs([
    "📈 1. Canlı Piyasa & Endeks Grafikleri (Market Overview)",
    "🤖 2. Evrensel Karar Motoru & Sinyal Botu",
    "🧭 3. SPK Yatırım Uygunluk & Risk Testi",
    "🌍 4. Çoklu Varlık Evreni (Seviye 1-5 Tüm Araçlar)",
    "📰 5. KAP Bildirim & Gemini AI Analizörü",
    "📊 6. Tarihsel Backtest & Mum Grafiği / PDF",
])

fetcher = MultiAssetDataFetcher(
    prefer_live_data=canli_veri_aktif,
    include_all_bist_from_tv=tum_bist_tara,
)
universe_snapshots = fetcher.fetch_all_universe(days=120)

kap_analyzer = KAPDisclosureAnalyzer(gemini_api_key=gemini_api_key or None, model_name=gemini_model)
kap_results_by_symbol: Dict[str, List[KAPAnalysisResult]] = {}
all_kap_results: List[KAPAnalysisResult] = []
for disc in st.session_state.custom_kap_list:
    res = kap_analyzer.analyze_disclosure(disc)
    all_kap_results.append(res)
    kap_results_by_symbol.setdefault(res.symbol, []).append(res)

decision_engine = AlpQuantDecisionEngine(
    suitability_profile=prof,
    total_capital_try=portfoy_bakiye,
)
all_signals: List[DecisionSignal] = [
    decision_engine.evaluate_asset(snap, kap_results_by_symbol.get(sym))
    for sym, snap in universe_snapshots.items()
]


with tab_market:
    st.subheader("📈 Canlı Piyasa Genel Bakış (BIST 100, BIST 30 & Küresel Varlıklar)")
    st.caption("AlpQuant v1 açılış paneli: Anlık endeks seviyeleri, 6 aylık tepe/dip bantları, 50 günlük hareketli ortalama trend analizi ve interaktif mum grafikleri:")

    sub_t1, sub_t2, sub_t3, sub_t4, sub_t5 = st.tabs([
        "🇹🇷 BIST 100 (XU100)",
        "💎 BIST 30 (XU030)",
        "🇺🇸 S&P 500 (SPY)",
        "🥇 Gram Altın (Spot TL)",
        "⚡ Bitcoin (BTC/USD)"
    ])

    def render_dashboard(df: pd.DataFrame, title: str, currency: str = "TL"):
        if df is None or df.empty or len(df) < 2:
            st.warning(f"⚠️ {title} verisi şu anda yüklenemedi.")
            return

        last_price = float(df['Close'].iloc[-1])
        prev_price = float(df['Close'].iloc[-2])
        pct_change = ((last_price - prev_price) / max(prev_price, 0.0001)) * 100
        high_6m = float(df['High'].max())
        low_6m = float(df['Low'].min())
        
        sma50_series = df['Close'].rolling(window=min(50, len(df)), min_periods=5).mean()
        sma50 = float(sma50_series.iloc[-1]) if not sma50_series.empty else last_price
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{title} Fiyatı", f"{last_price:,.2f} {currency}", f"{pct_change:+.2f}%")
        c2.metric("6 Aylık Zirve (6M High)", f"{high_6m:,.2f} {currency}")
        c3.metric("6 Aylık Dip (6M Low)", f"{low_6m:,.2f} {currency}")
        
        trend_status = "🟢 Boğa (Bullish)" if not pd.isna(sma50) and last_price >= sma50 else "🔴 Ayı (Bearish)"
        dist_to_high = ((high_6m - last_price) / max(high_6m, 0.01)) * 100
        c4.metric("50 Günlük Trend", trend_status, f"Zirveye Uzaklık: %{dist_to_high:.1f}")
        
        commentary = f"**Piyasa Analitiği:** **{title}** endeksi 50 günlük hareketli ortalamasına (SMA50: {sma50:,.2f} {currency}) kıyasla **{trend_status}** bölgede seyretmektedir. "
        if dist_to_high < 2.5:
            commentary += f"Fiyat 6 aylık zirvesine ({high_6m:,.2f} {currency}) son derece yakın olup güçlü yukarı yönlü momentumu sürdürmektedir."
        elif dist_to_high > 12.0:
            commentary += f"Fiyat 6 aylık zirvesinden **%{dist_to_high:.1f}** oranında çekilmiş olup dip konsolidasyon ve toparlanma aşamasındadır."
        else:
            commentary += f"Fiyat 6 aylık zirvesinin **%{dist_to_high:.1f}** altında dengeli işlem görmektedir."
        
        st.markdown(
            f"<div style='background-color:#1E222D; padding:14px 18px; border-radius:8px; border-left: 4px solid #2962FF; margin-top:10px; margin-bottom: 18px; color: #D1D4DC; font-size: 14.5px;'>{commentary}</div>",
            unsafe_allow_html=True
        )

        fig_idx = go.Figure(data=[go.Candlestick(
            x=df.index, open=df['Open'], high=df['High'],
            low=df['Low'], close=df['Close'],
            increasing_line_color='#089981', decreasing_line_color='#F23645',
            name=title
        )])
        if len(df) >= 20:
            sma20_series = df['Close'].rolling(window=20, min_periods=5).mean()
            fig_idx.add_trace(go.Scatter(
                x=df.index, y=sma20_series,
                mode='lines', line=dict(color='#2962FF', width=1.5), name='SMA 20'
            ))
            fig_idx.add_trace(go.Scatter(
                x=df.index, y=sma50_series,
                mode='lines', line=dict(color='#FF9800', width=1.5), name='SMA 50'
            ))
        fig_idx.update_layout(
            template="plotly_dark", paper_bgcolor="#131722", plot_bgcolor="#131722",
            margin=dict(l=10, r=10, t=10, b=10), xaxis_rangeslider_visible=False,
            xaxis=dict(showgrid=True, gridcolor="#2A2E39"),
            yaxis=dict(showgrid=True, gridcolor="#2A2E39", side="right"),
            height=480,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_idx, use_container_width=True)

    def safe_get_market_index_data(ticker: str, fallback_symbol: str, base_price: float, risk_level: int = 3) -> pd.DataFrame:
        df = None
        if canli_veri_aktif and HAS_YFINANCE:
            try:
                hist = yf.Ticker(ticker).history(period="6mo")
                if hist is not None and not hist.empty and len(hist) >= 15:
                    df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
                    if fallback_symbol == "XAU_TRY":
                        scale = base_price / float(df["Close"].iloc[-1])
                        for col in ("Open", "High", "Low", "Close"):
                            df[col] = np.round(df[col] * scale, 4)
            except Exception as exc:
                logger.debug("Live data fetch error for %s: %s", ticker, exc)
                df = None
        if df is None or df.empty or len(df) < 15:
            fetcher_inst = MultiAssetDataFetcher(prefer_live_data=False)
            df = fetcher_inst.generate_synthetic_ohlcv(symbol=fallback_symbol, base_price=base_price, days=120, risk_level=risk_level)
        return df

    with sub_t1:
        xu100_df = safe_get_market_index_data("XU100.IS", "XU100", 9250.0, risk_level=3)
        render_dashboard(xu100_df, "BIST 100 (XU100)", "TL")
    with sub_t2:
        xu030_df = safe_get_market_index_data("XU030.IS", "XU030", 10180.0, risk_level=3)
        render_dashboard(xu030_df, "BIST 30 (XU030)", "TL")
    with sub_t3:
        spy_df = safe_get_market_index_data("SPY", "SPY", 585.0, risk_level=3)
        render_dashboard(spy_df, "S&P 500 ETF (SPY)", "USD")
    with sub_t4:
        xau_df = safe_get_market_index_data("GC=F", "XAU_TRY", 3050.0, risk_level=1)
        render_dashboard(xau_df, "Gram Altın (Spot TL)", "TL")
    with sub_t5:
        btc_df = safe_get_market_index_data("BTC-USD", "BTC-USD", 68500.0, risk_level=5)
        render_dashboard(btc_df, "Bitcoin (BTC/USD)", "USD")

with tab_karar:
    st.subheader("🤖 Otonom Karar Mekanizması (Tüm Piyasa Varlıkları: Seviye 1 - 5)")
    st.markdown(
        "Tek bir kuruma bağlı kalmadan; **Para Piyasası Fonları (PPF)**, **Tahvil/Eurobond/Sukuk**, "
        "**Döviz & Altın/Gümüş**, **Tüm TEFAS Fonları**, **BIST 30 / BIST 100 Hisseleri**, "
        "**ABD Endeks/Teknoloji ETF & Hisseleri (`SPY`, `QQQ`, `NVDA`)** ve **Kripto / Kaldıraçlı Varlıkları (`BTC`, `ETH`, `SOL`, `TQQQ`)** "
        "tarayarak anlık sinyal, ATR Stop-Loss / Take-Profit ve pozisyon büyüklüğü üretir."
    )

    seviye_filtre = st.multiselect(
        "Risk Seviyesine Göre Filtrele",
        options=[1, 2, 3, 4, 5],
        default=prof.approved_levels,
        format_func=lambda x: f"Seviye {x}",
    )

    filtered_signals = [s for s in all_signals if s.risk_level in seviye_filtre]
    buy_signals = [s for s in filtered_signals if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY)]
    restricted_signals = [
        s for s in filtered_signals
        if s.action in (SignalAction.RESTRICTED_SPK, SignalAction.FILTERED_PARTICIPATION, SignalAction.VETO_KAP)
    ]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Taranan Evrensel Varlık", f"{len(filtered_signals)}")
    m2.metric("Aktif AL / GÜÇLÜ AL Sinyali", f"{len(buy_signals)}")
    m3.metric("SPK Onaylı Seviyeleriniz", f"{prof.approved_levels}")
    m4.metric("Kısıtlı / Veto Varlık", f"{len(restricted_signals)}")

    signal_rows = []
    for s in sorted(filtered_signals, key=lambda x: x.composite_score, reverse=True):
        signal_rows.append({
            "Kod": s.symbol,
            "Varlık Adı": s.name,
            "Risk Seviyesi": f"Seviye {s.risk_level}",
            "Varlık Sınıfı": s.asset_class,
            "Karar Sinyali": s.action.value,
            "Bileşik Skor": s.composite_score,
            "Teknik": s.technical_score,
            "Temel/TER": s.fundamental_score,
            "KAP AI": s.kap_sentiment_score,
            "Anlık Fiyat": f"{s.current_price:,.2f} {s.currency}",
            "Önerilen Adet": s.recommended_quantity,
            "Emir Tutarı (TL)": s.recommended_notional_try,
            "Stop-Loss": s.stop_loss_price,
            "Take-Profit": s.take_profit_price,
            "Karar Gerekçesi": " | ".join(s.rationale[:3]),
        })

    st.dataframe(pd.DataFrame(signal_rows), use_container_width=True, hide_index=True)

    col_mail_btn, _ = st.columns([1, 2])
    with col_mail_btn:
        if st.button("📧 Sinyalleri E-Posta Bildirimi Olarak Gönder", use_container_width=True):
            ok, msg = EmailSignalNotifier.send_email_alert(
                recipient_email=alici_eposta,
                suitability=prof,
                signals=filtered_signals,
                sender_email=gonderici_eposta,
                smtp_app_password=smtp_sifre or None,
            )
            if ok:
                st.success(f"✅ {msg}")
            else:
                st.info(f"ℹ️ {msg} (Aşağıda hazır HTML e-posta çıktısını inceleyebilirsiniz.)")

    with st.expander("📨 E-Posta Bildirim Raporu Canlı Önizleme (HTML)"):
        st.components.v1.html(
            EmailSignalNotifier.build_html_report(prof, filtered_signals),
            height=420,
            scrolling=True,
        )

with tab_test:
    st.markdown(
        "<div class='aq-card'><h3 style='margin:0;color:#FFFFFF !important;'>🧭 Evrensel SPK Yatırım Uygunluk ve Risk Profilleme Testi</h3>"
        "<p style='margin:6px 0 0 0;'>Tek bir kurumun ürünleriyle sınırlı kalmaksızın, <b>Seviye 1'den Seviye 5'e kadar tüm yerel ve küresel yatırım araçlarında</b> (TEFAS Fonları, BIST Hisseleri, Küresel ETF/ABD Hisseleri, Tahvil/Eurobond/Sukuk, Altın/Döviz ve Kripto/Türev) işlem uygunluğunuzu belirler ve Karar Motoruna bağlar.</p></div>",
        unsafe_allow_html=True,
    )

    if st.session_state.step == 1:
        with st.form("etap1_form"):
            st.subheader("1. Etap: Yatırımcı Bilgileri ve Risk Tercihi")
            col1, col2 = st.columns(2)
            with col1:
                st.selectbox("Yaş", ["Seçiniz", "18-30 Yaş", "31-50 Yaş", "51-65 Yaş", "66 ve üzeri", "Kurumsal Müşteri"], key="yas", index=1)
                st.selectbox("Eğitim Durumu", ["Seçiniz", "İlköğretim/Ortaöğretim", "Lise", "Lisans ve Üstü", "Kurumsal Müşteri"], key="egitim", index=3)
            with col2:
                st.selectbox("Meslek", meslekler_listesi, key="meslek", index=meslekler_listesi.index("Öğrenci"))
                st.selectbox("Yatırım Değerlendirme Süresi", ["Seçiniz", "Kısa Vadeli (0-1 Yıl)", "Orta Vadeli (1-3 Yıl)", "Uzun Vadeli (3 Yıl ve Üzeri)"], key="sure", index=3)

            st.selectbox("Yatırım Tecrübesi", ["Seçiniz", "1 yıldan az", "1-4 yıl", "5-10 yıl", "10 yıldan fazla"], key="tecrube", index=2)
            st.subheader("Risk ve Getiri Tercihi Seçimi (100.000 TL Senaryosu)")
            st.radio(
                "Senaryo Seçiminiz:",
                [
                    "1. 100.000 TL anaparamdan hiç kayıp yaşamak istemem. Para Piyasası Fonları, Mevduat/TLREF ve Kısa Vadeli Bono/Sukuk ile ilerlerim.",
                    "2. 100.000 TL yatırımımda maksimum %5 geçici düşüşe dayanabilirim. Eurobond, ABD Tahvil ETF (SHY/TLT) ve Borçlanma Fonlarını tercih ederim.",
                    "3. 100.000 TL yatırımımda uzun vadede %15-%20 dalgalanmaları normal karşılar; BIST 30 Mavi Çip, S&P 500 ETF (SPY) ve Altın/Değişken fonlarda beklerim.",
                    "4. Yüksek getiri uğruna %30-%40 erimeyi göğüsleyebilirim. BIST 100 Büyüme Hisseleri, TEFAS Hisse/Teknoloji Fonları (MAC, IIH, AFT) ve Nasdaq (QQQ, NVDA) yatırımı yaparım.",
                    "5. Çok yüksek getiri için yüksek volatilite ve anapara kaybı riskini alır; Kripto (BTC, ETH, SOL), 3x Kaldıraçlı ETF (TQQQ), Serbest Fonlar ve Yüksek Beta hisselerde işlem yaparım.",
                ],
                key="risk_tercihi",
                index=4,
            )
            if st.form_submit_button("İleri ➡️ (Ürün Bilgisi ve İşlem Tecrübesi)"):
                st.session_state.kalici_risk_tercihi = st.session_state.risk_tercihi
                st.session_state.step = 2
                st.rerun()

    elif st.session_state.step == 2:
        st.subheader("2. Etap: 5 Evrensel Risk Seviyesinde Bilgi ve İşlem Tecrübesi")
        urun_tanimlari = [
            ("1/5 - Çok Düşük Riskli Varlıklar", "TEFAS Para Piyasası Fonları (TI1, RPP), Mevduat/Katılma, Hazine Bonosu/Sukuk, Spot Altın/Döviz"),
            ("2/5 - Düşük Riskli Varlıklar", "TEFAS Eurobond & Borçlanma Fonları (DBH, AKE, IPV), ABD Hazine Tahvili ETF'leri (SHY, TLT, SPSK)"),
            ("3/5 - Orta Riskli Varlıklar", "BIST 30 Mavi Çip Hisseler, TEFAS Altın/Değişken Fonlar (TTA, TCD), Küresel Endeks ETF'leri (SPY, SPUS)"),
            ("4/5 - Yüksek Riskli Varlıklar", "BIST 100 Büyüme/Sektör Hisseleri, TEFAS Hisse Yoğun/Teknoloji Fonları (MAC, IIH, AFT), Nasdaq & ABD Hisseleri (QQQ, NVDA)"),
            ("5/5 - Çok Yüksek Riskli Varlıklar", "Kripto Varlıklar (BTC, ETH, SOL), 3x Kaldıraçlı ETF (TQQQ), TEFAS Serbest Fonlar (IPB, KAV), VİOP ve Yüksek Beta Hisseler"),
        ]
        with st.form("etap2_form"):
            for idx, (baslik, alt) in enumerate(urun_tanimlari, start=1):
                st.markdown(f"**{baslik}** — *{alt}*")
                st.radio(
                    f"Seviye {idx} Bilgi Seviyeniz:",
                    [
                        "Bilgim yok: Bu ürünlerde daha önce işlem yapmadım.",
                        "Bilgim kısıtlı: İsimlerini duydum ancak detaylı fiyatlama/bilanço analizi yapamam.",
                        "Yeterince bilgim var: Getiri/risk mekanizmalarını biliyor ve aktif olarak yönetebiliyorum.",
                    ],
                    key=f"bilgi_{idx}",
                    index=2,
                )
                c1, c2 = st.columns(2)
                with c1:
                    st.selectbox(f"Seviye {idx} İşlem Sıklığı", ["Yılda birkaç kez", "Ayda birkaç kez", "Haftada birkaç kez"], key=f"siklik_{idx}", index=2)
                with c2:
                    st.selectbox(f"Seviye {idx} Hacim (TL)", ["1 TL - 50.000 TL", "50.001 TL - 500.000 TL", "500.001 TL ve üzeri"], key=f"hacim_{idx}", index=2)
                st.markdown("<hr>", unsafe_allow_html=True)

            if st.form_submit_button("✅ Testi Tamamla ve Tüm Piyasa Evrenini Karar Motoruna Bağla"):
                r_skor = int(st.session_state.kalici_risk_tercihi[0])
                ans_dict = {
                    i: {
                        "bilgi": st.session_state[f"bilgi_{i}"],
                        "siklik": st.session_state[f"siklik_{i}"],
                        "hacim": st.session_state[f"hacim_{i}"],
                    }
                    for i in range(1, 6)
                }
                st.session_state.suitability_profile = UniversalSuitabilityEngine.evaluate(
                    user_id="alperen_gulluoglu",
                    risk_skoru=r_skor,
                    answers=ans_dict,
                    participation_only=sadece_katilim_filtre,
                )
                st.session_state.step = 3
                st.rerun()

        if st.button("⬅️ 1. Etaba Geri Dön"):
            st.session_state.step = 1
            st.rerun()

    else:
        st.success("✅ Yatırım Uygunluk Testi tamamlandı ve onaylanan tüm varlık sınıfları Karar Motoruna bağlandı!")
        c1, c2, c3 = st.columns(3)
        c1.metric("Risk Skorunuz", f"{prof.risk_score} / 5")
        c2.metric("Erişime Açık Seviye Sayısı", f"{len(prof.approved_levels)}")
        c3.metric("Kısıtlanan Seviye Sayısı", f"{len(prof.restricted_levels)}")

        for gr in prof.group_results:
            badge = "<span class='aq-badge-ok'>✅ Erişime Açık</span>" if gr.is_suitable else "<span class='aq-badge-lock'>🔒 Kısıtlı</span>"
            st.markdown(
                f"<div class='aq-card'><div style='display:flex;justify-content:space-between;align-items:center;'>"
                f"<b>{gr.group_name}</b> {badge}</div>"
                f"<div style='font-size:13px;margin-top:6px;color:#B0B6C4;'>Sistem Gerekçesi: <b>{gr.reason_text}</b><br>"
                f"<b>Bu Seviyedeki Piyasa Araçları:</b> {' • '.join(gr.recommended_products)}</div></div>",
                unsafe_allow_html=True,
            )

        st.info(f"💡 **Davranışsal Finans Profiliniz:** {prof.behavioral_profile_text}")
        if st.button("🔄 Uygunluk Testini Baştan Düzenle"):
            st.session_state.step = 1
            st.rerun()

with tab_varliklar:
    st.subheader("🌍 Seviye 1 - 5 Evrensel Çoklu Varlık Veri Merkezi")
    asset_table = []
    for sym, snap in universe_snapshots.items():
        m = QuantitativeIndicators.compute_metrics(snap.ohlcv_history)
        asset_table.append({
            "Sembol": sym,
            "Varlık / Fon Adı": snap.name,
            "Risk Seviyesi": f"Seviye {snap.risk_level}",
            "SPK Durumu": "✅ Açık" if snap.risk_level in prof.approved_levels else "🔒 Kısıtlı",
            "Varlık Sınıfı": snap.asset_class.value,
            "Katılım Uyumlu": "Evet" if snap.is_participation_compliant else "Konvansiyonel",
            "Son Fiyat": f"{snap.current_price:,.2f} {snap.currency}",
            "RSI (14)": snap.rsi_tv if snap.rsi_tv is not None else m["rsi_14"],
            "1 Aylık Dibe Uzaklık (%)": f"%{m['dist_from_monthly_low_pct']:.2f}",
            "F/K": snap.pe_ratio if snap.pe_ratio is not None else "-",
            "ROE (%)": snap.roe_pct if snap.roe_pct is not None else "-",
            "Fon Gideri (TER %)": snap.expense_ratio_pct if snap.expense_ratio_pct is not None else "-",
        })
    st.dataframe(pd.DataFrame(asset_table), use_container_width=True, hide_index=True)

with tab_kap:
    st.subheader("📰 KAP Bildirimlerini Olumlu / Olumsuz Analiz Eden AI Motoru")
    with st.form("yeni_kap_form"):
        st.markdown("**Yeni KAP Bildirimi veya Piyasa Haberi Analiz Et (Gemini AI + Kural Motoru)**")
        c1, c2 = st.columns([1, 3])
        with c1:
            yeni_sembol = st.selectbox("Varlık / Hisse Kodu", list(universe_snapshots.keys()))
        with c2:
            yeni_baslik = st.text_input("KAP Bildirim Başlığı", value="Yeni İş İlişkisi — İhracat Sözleşmesi İmzalanması")
        yeni_metin = st.text_area(
            "KAP Bildirim Özeti / Metni",
            value="Şirketimiz yurt dışında yerleşik bir müşteri ile yeni satış sözleşmesi imzalamıştır. Sözleşmenin son yıllık ciroya oranı %18,5'tir.",
        )
        if st.form_submit_button("🔍 KAP Bildirimini Analiz Et ve Karar Motoruna Gönder"):
            new_disc = KAPDisclosure(
                disclosure_id=f"KAP-CUSTOM-{len(st.session_state.custom_kap_list)+1}",
                symbol=yeni_sembol,
                company_name=yeni_sembol,
                title=yeni_baslik,
                summary_text=yeni_metin,
                published_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
            st.session_state.custom_kap_list.insert(0, new_disc)
            st.rerun()

    kap_df_rows = []
    for r in all_kap_results:
        kap_df_rows.append({
            "Bildirim No": r.disclosure_id,
            "Sembol": r.symbol,
            "Etki Sınıfı": r.sentiment_label,
            "Duygu Skoru (-1..+1)": r.sentiment_score,
            "Güven": r.confidence,
            "Kategori": r.category,
            "Ciroya Oran (%)": f"%{r.extracted_financial_ratio_pct}" if r.extracted_financial_ratio_pct else "-",
            "Hard Veto (İşlem Engeli)": "🚨 EVET" if r.is_hard_veto else "Hayır",
            "Analiz Motoru": r.analysis_engine,
            "Tespit Edilen Tetikleyiciler": " | ".join(r.matched_triggers),
        })
    st.dataframe(pd.DataFrame(kap_df_rows), use_container_width=True, hide_index=True)

with tab_screener:
    st.subheader("📈 Tarihsel Veri Analizi (Backtest) & Mum Grafiği / PDF Araştırma Raporu")
    backtester = TimeSeriesBacktester()
    bt_rows = []
    for sym, snap in universe_snapshots.items():
        rep = backtester.run_backtest(sym, snap.ohlcv_history, name=snap.name, risk_level=snap.risk_level)
        bt_rows.append({
            "Sembol": rep.symbol,
            "Varlık Adı": rep.name,
            "Risk Seviyesi": f"Seviye {rep.risk_level}",
            "AlpQuant Strateji Getirisi (%)": f"%{rep.total_return_pct:+.2f}",
            "Al ve Tut Getirisi (%)": f"%{rep.benchmark_buy_hold_pct:+.2f}",
            "Sharpe Oranı": rep.sharpe_ratio,
            "Max Drawdown (%)": f"%{rep.max_drawdown_pct:.2f}",
            "Kazanma Oranı (%)": f"%{rep.win_rate_pct:.1f}",
            "Toplam İşlem": rep.total_trades,
        })
    st.dataframe(pd.DataFrame(bt_rows), use_container_width=True, hide_index=True)

    st.markdown("<hr>", unsafe_allow_html=True)
    secilen_varlik = st.selectbox("Detaylı Mum Grafiği ve PDF Araştırma Raporu İçin Varlık Seçin", list(universe_snapshots.keys()))
    if secilen_varlik:
        snap_sel = universe_snapshots[secilen_varlik]
        hist_df = snap_sel.ohlcv_history
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=hist_df.index,
                    open=hist_df["Open"],
                    high=hist_df["High"],
                    low=hist_df["Low"],
                    close=hist_df["Close"],
                    increasing_line_color="#089981",
                    decreasing_line_color="#F23645",
                )
            ]
        )
        fig.update_layout(
            title=f"{snap_sel.symbol} — {snap_sel.name} (6 Aylık Fiyat Serisi)",
            template="plotly_dark",
            paper_bgcolor="#131722",
            plot_bgcolor="#131722",
            xaxis_rangeslider_visible=False,
            height=460,
        )
        st.plotly_chart(fig, use_container_width=True)

        if HAS_FPDF:
            rapor_dict = {
                "Ticker": snap_sel.symbol,
                "Sector": snap_sel.sector,
                "Price": f"{snap_sel.current_price} {snap_sel.currency}",
                "P/E": snap_sel.pe_ratio or "N/A",
                "P/B": snap_sel.pb_ratio or "N/A",
                "ROE%": snap_sel.roe_pct or "N/A",
                "PEG": snap_sel.peg_ratio or "N/A",
                "Beta": snap_sel.beta or "N/A",
            }
            pdf_bytes = generate_pdf_file(snap_sel.symbol, rapor_dict)
            st.download_button(
                label=f"📥 PDF Araştırma Raporunu İndir ({snap_sel.symbol})",
                data=pdf_bytes,
                file_name=f"{snap_sel.symbol}_AlpQuant_Report.pdf",
                mime="application/pdf",
            )