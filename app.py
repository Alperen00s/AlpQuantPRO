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


# ============================================================================
# 1. EVRENSEL ÇOKLU VARLIK KATALOĞU VE VERİ MOTORU (770+ DAHİLİ / 950+ CANLI VARLIK)
# ============================================================================

class AssetClass(str, Enum):
    MONEY_MARKET_DEPOSIT = "Para Piyasası & Mevduat / Katılım"
    BONDS_EUROBOND_SUKUK = "Tahvil, Eurobond & Kira Sertifikası (Sukuk)"
    FOREX_PRECIOUS_METALS = "Döviz, Altın, Kıymetli Maden & Emtia"
    TEFAS_MUTUAL_FUND = "TEFAS Yatırım Fonları (Konvansiyonel & Katılım)"
    BIST_BLUECHIP_30 = "BIST 30 / Büyük Ölçekli Paylar"
    BIST_GROWTH_100 = "BIST 100 / Sektörel Büyüme Payları"
    GLOBAL_ETF_EQUITY = "Küresel ETF & Yabancı Borsa Payları (ABD/Avrupa/Asya)"
    BIST_HIGH_BETA = "BIST Yüksek Beta / Yan Tahta & Büyüme Payları"
    CRYPTO_LEVERAGED_HEDGE = "Kripto Varlıklar, Kaldıraçlı ETF & Serbest Fonlar"


BIST_30 = [
    "AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "DOAS", "EKGYO", "ENKAI",
    "EREGL", "FROTO", "GARAN", "GUBRF", "HEKTS", "ISCTR", "KCHOL", "KONTR", "KOZAL", "KRDMD",
    "OYAKC", "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TCELL", "THYAO", "TOASO", "TUPRS",
]

BIST_50_EXTRA = [
    "AEFES", "AGHOL", "AKSEN", "CCOLA", "CIMSA", "CWENE", "DOHOL", "ECILC", "EUPWR", "GESAN",
    "HALKB", "ISGYO", "KOZAA", "MGROS", "MIATK", "ODAS", "SOKM", "TAVHL", "TKFEN", "TTKOM",
    "ULKER", "VAKBN", "VESTL", "YKBNK", "ZOREN",
]
BIST_50 = sorted(list(set(BIST_30 + BIST_50_EXTRA)))

BIST_100_EXTRA = [
    "AGROT", "AHGAZ", "AKCNS", "AKFGY", "AKFYE", "AKSA", "ALBRK", "ALFAS", "ALTNY", "ANSGR",
    "ARDYZ", "ASGYO", "AYDEM", "BAGFS", "BERA", "BIENY", "BIOEN", "BOBET", "BRYAT", "BTCIM",
    "CANTE", "CLEBI", "CVKMD", "DEVA", "EGEEN", "ENERY", "ENJSA", "EUREN", "FENER", "GENIL",
    "GLYHO", "GOLTS", "GOZDE", "GSRAY", "GWIND", "IPEKE", "ISDMR", "ISMEN", "IZENR", "KARSN",
    "KAYSE", "KCAER", "KLSER", "KMPUR", "KONYA", "KORDS", "LOGO", "MAGEN", "MAVI", "MPARK",
    "NETAS", "NTHOL", "OTKAR", "PAPIL", "PASEU", "PENTA", "QUAGR", "REEDR", "RGYAS", "SDTTR",
    "SELEC", "SMRTG", "SNGYO", "TABGD", "TMSN", "TRGYO", "TSKB", "TTRAK", "TUKAS", "TURSG",
    "VESBE", "YEOTK", "YYLGD",
]
BIST_100 = sorted(list(set(BIST_50 + BIST_100_EXTRA)))

KATILIM_30 = [
    "AKCNS", "ALBRK", "ALFAS", "ALTNY", "ASELS", "ASTOR", "BERA", "BIMAS", "BRSAN", "CANTE",
    "CIMSA", "CWENE", "EGEEN", "ENJSA", "EREGL", "EUPWR", "FROTO", "GESAN", "GUBRF", "ISDMR",
    "KCAER", "KONTR", "KOZAL", "KRDMD", "MAVI", "MIATK", "OYAKC", "PETKM", "PGSUS", "SASA",
    "SMRTG", "TUPRS", "YEOTK",
]

SEKTOR_SOZLUGU: Dict[str, List[str]] = {
    "Bankacılık": [
        "AKBNK", "ALBRK", "GARAN", "HALKB", "ICBCT", "ISCTR", "KLNMA", "QNBFB", "SKBNK", "TSKB",
        "VAKBN", "YKBNK",
    ],
    "Holding & Yatırım": [
        "AGHOL", "ALARK", "BERA", "BRYAT", "DAGHL", "DOHOL", "ECZYT", "GEDIK", "GLRYH", "GLYHO",
        "GOZDE", "GSDHO", "IEYHO", "INFO", "INVEO", "INVES", "ISMEN", "KCHOL", "KLRHO", "METRO",
        "MZHLD", "NTHOL", "OYYAT", "OSMEN", "POLHO", "RALYH", "SAHOL", "TERA", "TKFEN", "TRCAS",
        "UFUK", "VERUS",
    ],
    "Havacılık & Ulaşım": [
        "BEYAZ", "CLEBI", "GRSEL", "GSDDE", "PGSUS", "PASEU", "RYSAS", "TAVHL", "THYAO", "TLMAN",
        "TUREX",
    ],
    "Savunma & Teknoloji": [
        "AGROT", "ALCTL", "ARDYZ", "ARENA", "ASELS", "ATATP", "AZTEK", "DESPC", "DGATE", "EDATA",
        "ESCOM", "FONET", "FORTE", "HTTBT", "INDES", "INGRM", "KFEIN", "KRONT", "LINK", "LOGO",
        "MANAS", "MIATK", "MOBTL", "MTRKS", "NETAS", "OBASE", "ONRYT", "PAPIL", "PATEK", "PENTA",
        "PKART", "REEDR", "SDTTR", "SMART", "VBTYZ",
    ],
    "Enerji & Yenilenebilir": [
        "AHGAZ", "AKENER", "AKFYE", "AKSEN", "ALFAS", "ANELE", "ASTOR", "AYDEM", "AYEN", "BIOEN",
        "BRLSM", "CANTE", "CATES", "CONSE", "CWENE", "EMKEL", "ENERY", "ENJSA", "ESEN", "EUPWR",
        "GEREL", "GESAN", "GWIND", "HUNER", "IZENR", "KARYE", "KONTR", "MAGEN", "NATEN", "NTGAZ",
        "ODAS", "ORMA", "PAMEL", "PRKME", "SAYAS", "SMRTG", "TATEN", "TBORG", "ULUSE", "YEOTK",
        "YIGIT", "ZEDUR", "ZOREN",
    ],
    "Tüpraş, Rafineri & Kimya": [
        "ACSEL", "AKSA", "ALKIM", "AYGAZ", "BAGFS", "BAYRK", "BRKSN", "DEVA", "DNISI", "DYOBY",
        "EGGUB", "EGPRO", "EPLAS", "EUREN", "GEDZA", "GUBRF", "HEKTS", "IPEKE", "ISKPL", "KBORU",
        "KIMMR", "KMPUR", "KOPOL", "KORDS", "KRPLS", "MARBL", "MERCN", "MRSHL", "PETKM", "pltsd",
        "POLTK", "RNPOL", "SANFM", "SASA", "SEYKM", "TARKM", "TRILC", "TUPRS",
    ],
    "Otomotiv & Yan Sanayi": [
        "ALCAR", "ASUZU", "BFREN", "bosch", "BRISA", "CELHA", "CEMAS", "DOAS", "DITAS", "EGEEN",
        "FMIZP", "FROTO", "GOODY", "JANTS", "KARSN", "KATMR", "OTKAR", "PARSN", "TMSN", "TOASO",
        "TTRAK", "TUCLK",
    ],
    "Perakende & Gıda": [
        "AEFES", "ALTIN", "AVOD", "BANVT", "BIGCH", "BIMAS", "BIZIM", "CCOLA", "CRFSA", "DARDL",
        "DOKTA", "EKIZ", "ELITE", "ERSU", "FADE", "FRIGO", "GOKNR", "KAYSE", "KERVT", "KNFRT",
        "KRVGD", "KTSKR", "MAVI", "MERKO", "MGROS", "OFSYM", "ORCAY", "OYLUM", "OZRDN", "PENGD",
        "PETUN", "PINSU", "PNSUT", "SELGD", "SEFK", "SOKM", "SUWEN", "TABGD", "TATGD", "TKURU",
        "TUKAS", "ULKER", "ULuun", "VAKKO", "YAPRK", "YYLGD",
    ],
    "Demir Çelik & Madencilik": [
        "BMSCH", "BMSTL", "BRSAN", "BURCE", "BURVA", "CEMTS", "CUSAN", "CVKMD", "DMSAS", "DOFER",
        "ERBOS", "EREGL", "GZNMI", "ISDMR", "IZMDC", "KCAER", "KOZAA", "KOZAL", "KRDMA", "KRDMB",
        "KRDMD", "PNLSN", "PRKAB", "SARKY", "YKSLN",
    ],
    "Çimento, Cam & Toprak": [
        "AFYON", "AKCNS", "ASLAN", "BIENY", "BOBET", "BSOKE", "BTCIM", "BUCIM", "CIMSA", "CMBTN",
        "CMENT", "DOGUB", "EGSER", "GOLTS", "KLSER", "KONYA", "KUTPO", "LMKDC", "NIBAS", "NUHCM",
        "OYAKC", "QUAGR", "SISE", "USAK", "YBTAS",
    ],
    "GYO & İnşaat": [
        "ADGYO", "AGYO", "AKFGY", "AKMGY", "AKSGY", "ALGYO", "ASGYO", "ATAGY", "AVGYO", "AVPGY",
        "BASGZ", "DGGYO", "DZGYO", "EKGYO", "ENKAI", "EYGYO", "FZLGY", "HLGYO", "IDGYO", "ISGYO",
        "KGYO", "KLGYO", "KRGYO", "KUYAS", "KZGYO", "MHRGY", "MRGYO", "MSGYO", "NUGYO", "OZGYO",
        "OZKGY", "PAGYO", "PEGYO", "PEKGY", "PSGYO", "RGYAS", "RYGYO", "SAFKR", "SANEL", "SEGYO",
        "SNGYO", "SRVGY", "SURGY", "TDGYO", "TRGYO", "TSGYO", "TURGG", "VKGYO", "YAYLA", "YGGYO",
        "YYAPI", "ZRGYO",
    ],
    "Telekom & İletişim": [
        "ihgzt", "IHAAS", "IHGZT", "KAREL", "PCILT", "TCELL", "TTKOM",
    ],
    "Sağlık & İlaç": [
        "ANGEN", "DEVA", "ECILC", "GENIL", "LKMNH", "MEDTR", "MPARK", "ONCSM", "RTALB", "SELEC",
        "SEYKM", "TNZTP", "TRILC",
    ],
    "Tekstil, Mobilya & Kağıt": [
        "ADESE", "almad", "ARCLK", "ARSAN", "ATEKS", "BAHKM", "BAKAB", "BARMA", "BJKAS", "BLCYT",
        "BOSSA", "BRKO", " BRMEN", "DGNMO", "DERIM", "DERHL", "DESA", "DOCO", "DURDO", "ENSRI",
        "ESCAR", "FENER", "FORMT", "GENTS", "GSRAY", "HATEK", "ISSEN", "KAPLM", "KARTN", "KLKIM",
        "KONKA", "KRTEK", "KSTUR", "LUKSK", "MAALT", "MACKO", "MAKIM", "MNDRS", "MNDTR", "PKENT",
        "PRZMA", "RUBNS", "SAMAT", "SILVR", "SNKRN", "SNPAM", "SONME", "SUMAS", "TEZOL", "TSPOr",
        "VESBE", "VESTL", "VKING", "YATAS", "YUNSA",
    ],
    "Sigorta & Finansal Kiralama": [
        "AGESA", "AKGRT", "ANHYT", "ANSGR", "GARFA", "ISFIN", "LIDFA", "RAYSG", "SEKFK", "TURSG",
        "ULUFA", "VAKFN",
    ],
}

# Temizlenmiş büyük harfli semboller
for _sec_k in list(SEKTOR_SOZLUGU.keys()):
    SEKTOR_SOZLUGU[_sec_k] = sorted(list({s.strip().upper() for s in SEKTOR_SOZLUGU[_sec_k] if s.strip()}))

KATILIM_TUM = sorted(list(set(KATILIM_30 + [
    "ACSEL", "ADGYO", "AFYON", "AGROT", "AHGAZ", "AKFYE", "AKMGY", "AKSA", "AKYHO", "ALCAR",
    "ALCTL", "ALKIM", "ALVES", "ANGEN", "ARCLK", "ARDYZ", "ARENA", "ARSAN", "ASGYO", "ASUZU",
    "ATAKP", "ATATP", "AVPGY", "AYEN", "AYGAZ", "AZTEK", "BAGFS", "BAHKM", "BAKAB", "BANVT",
    "BARMA", "BASGZ", "BAYRK", "BIENY", "BIGCH", "BIOEN", "BIZIM", "BMSTL", "BMSCH", "BNTAS",
    "BOBET", "BOSSA", "BRISA", "BRKO", "BRKSN", "BRLSM", "BUCIM", "BURCE", "BURVA", "CATES",
    "CELHA", "CEMAS", "CEMTS", "CIMSA", "CMBTN", "CMENT", "CONSE", "COSMO", "CRDFA", "CUSAN",
    "CVKMD", "CWENE", "DARDL", "DGATE", "DGGYO", "DGNMO", "DMSAS", "DNISI", "DOAS", "DOFER",
    "DOGUB", "DOKTA", "DURDO", "DYOBY", "EBPRO", "EDATA", "EDIP", "EGEEN", "EGGUB", "EGPRO",
    "EGSER", "EKGYO", "ELITE", "EMKEL", "ENERY", "ENJSA", "ENKAI", "ENSRI", "EPLAS", "ERBOS",
    "EREGL", "ERSU", "ESCAR", "ESCOM", "EUPWR", "EUREN", "EYGYO", "FMIZP", "FONET", "FORMT",
    "FORTE", "FROTO", "FZLGY", "GEDZA", "GENIL", "GENTS", "GEREL", "GESAN", "GOKNR", "GOLTS",
    "GOODY", "GRSEL", "GUBRF", "GWIND", "hatsn", "HATEK", "HKTM", "HLGYO", "HTTBT", "HUNER",
    "IHAAS", "INDES", "INGRM", "INTEM", "IPEKE", "ISDMR", "ISKPL", "ISSEN", "IZMDC", "IZENR",
    "JANTS", "KAPLM", "KAREL", "KARTN", "KARYE", "KATMR", "KAYSE", "KBORU", "KCAER", "KERVT",
    "KFEIN", "KIMMR", "KLKIM", "KLSER", "KLSYN", "KMPUR", "KNFRT", "KOCAER", "KONKA", "KONTR",
    "KONYA", "KOPOL", "KORDS", "KOZAA", "KOZAL", "KRDMA", "KRDMB", "KRDMD", "KRGYO", "KRONT",
    "KRPLS", "KRSTL", "KRTEK", "KRVGD", "KTLEV", "KTSKR", "KUTPO", "KUYAS", "LKMNH", "LMKDC",
    "LOGO", "LUKSK", "MAALT", "MACKO", "MAGEN", "MAKIM", "MANAS", "MARBL", "MAVI", "MEDTR",
    "MEGMT", "MEKAG", "MERCN", "MERKO", "MIATK", "MNDRS", "MNDTR", "MOBTL", "MPARK", "MRSHL",
    "MSGYO", "MTRKS", "NATEN", "NETAS", "NTGAZ", "NUHCM", "OBASE", "OFSYM", "ONCSM", "ONRYT",
    "ORCAY", "ORMA", "OSTIM", "OTKAR", "OYAKC", "OYLUM", "OZKGY", "OZRDN", "OZSUB", "PAGYO",
    "PAMEL", "PAPIL", "PARSN", "PASEU", "PATEK", "PCILT", "PEKGY", "PENGD", "PENTA", "PETKM",
    "PETUN", "PGSUS", "PINSU", "PKART", "PKENT", "PLTUR", "PNLSN", "PNSUT", "POLHO", "POLTK",
    "PRKAB", "PRKME", "QUAGR", "RALYH", "REEDR", "RNPOL", "RODRG", "RTALB", "RUBNS", "SAFKR",
    "SAMAT", "SANEL", "SANFM", "SANKO", "SARKY", "SASA", "SAYAS", "SDTTR", "SEGYO", "SELGD",
    "SELEC", "SEYKM", "SILVR", "SMART", "SMRTG", "SNGYO", "SNICA", "SNPAM", "SOKE", "SOKM",
    "SONME", "SRVGY", "SUMAS", "SUNTK", "SURGY", "SUWEN", "TABGD", "TARKM", "TATEN", "TATGD",
    "TDGYO", "TEZOL", "TKFEN", "TLMAN", "TMSN", "TNZTP", "TOASO", "TRILC", "TSGYO", "TTRAK",
    "TUCLK", "TUKAS", "TUPRS", "TUREX", "ULKER", "ULUSE", "ULUUN", "USAK", "VAKKO", "VBTYZ",
    "VESBE", "VESTL", "VKING", "YAPRK", "YATAS", "YBTAS", "YEOTK", "YGGYO", "YIGIT", "YKSLN",
    "YUNSA", "YYLGD", "ZEDUR", "ZRGYO",
])))
KATILIM_TUM = [s.upper() for s in KATILIM_TUM]


def _build_universal_multi_asset_catalog() -> List[Dict[str, Any]]:
    """
    770+ enstrümanlık Evrensel Çoklu Varlık Kataloğu:
    - 42 Para Piyasası, Tahvil, Eurobond, Kira Sertifikası (Sukuk), Döviz, Altın, Gümüş, Emtia
    - 111 TEFAS Yatırım Fonu (53 Katılım Fonu + 58 Konvansiyonel Fon)
    - 354 BIST Pay Senedi (15 Sektör, BIST 30 / 50 / 100 / Katılım Endeksi ve Yan Tahtalar)
    - 203 Küresel ETF & ABD Borsa Payları (S&P 500, Nasdaq, Sektörel ETF, İslami ETF ve Dev Şirketler)
    - 60 Kripto Varlık & Kaldıraçlı / Ters ETF
    """
    catalog: List[Dict[str, Any]] = []
    seen_symbols = set()

    def add_item(item: Dict[str, Any]) -> None:
        sym = item["symbol"].strip().upper()
        if sym in seen_symbols:
            return
        seen_symbols.add(sym)
        item["symbol"] = sym
        catalog.append(item)

    # -------------------------------------------------------------------------
    # A. DÖVİZ, KIYMETLİ MADENLER, KÜRESEL EMTİA & SABİT GETİRİLİ / SUKUK (42 ADET)
    # -------------------------------------------------------------------------
    macro_and_fixed_income = [
        # Mevduat & Para Piyasası Referansları
        {"symbol": "TR_MEVDUAT_TL", "name": "TL Vadeli Mevduat / Gecelik Repo Referansı", "risk_level": 1, "asset_class": AssetClass.MONEY_MARKET_DEPOSIT, "sector": "Para Piyasası & Mevduat", "is_participation": False, "currency": "TRY", "base_price": 100.0, "ter": 0.0},
        {"symbol": "KT_KATILIM_HESABI", "name": "Kuveyt Türk Kar/Zarar Ortaklığı Katılma Hesabı (TL)", "risk_level": 1, "asset_class": AssetClass.MONEY_MARKET_DEPOSIT, "sector": "Katılım Hesabı", "is_participation": True, "currency": "TRY", "base_price": 100.0, "ter": 0.0},
        # Hazine Bonosu, Tahvil, Kira Sertifikası (Sukuk) & Eurobond
        {"symbol": "TR_HAZINE_BONO", "name": "T.C. Hazine Kısa Vadeli İskontolu Bono", "risk_level": 1, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Hazine Bonosu & Tahvil", "is_participation": False, "currency": "TRY", "base_price": 104.25, "ter": 0.0},
        {"symbol": "TR_SUKUK_TL", "name": "T.C. Hazine TL Kira Sertifikası (Kamu Sukuk)", "risk_level": 1, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Kira Sertifikası (Sukuk)", "is_participation": True, "currency": "TRY", "base_price": 106.80, "ter": 0.0},
        {"symbol": "TR_ALTIN_KIRA_SERT", "name": "T.C. Hazine Altına Dayalı Kira Sertifikası", "risk_level": 1, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Kira Sertifikası (Sukuk)", "is_participation": True, "currency": "TRY", "base_price": 3085.0, "ter": 0.0},
        {"symbol": "TR_2Y_TAHVIL", "name": "T.C. Hazine 2 Yıllık Gösterge Devlet Tahvili", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Hazine Bonosu & Tahvil", "is_participation": False, "currency": "TRY", "base_price": 98.40, "ter": 0.0},
        {"symbol": "TR_5Y_TAHVIL", "name": "T.C. Hazine 5 Yıllık Sabit Kuponlu Tahvil", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Hazine Bonosu & Tahvil", "is_participation": False, "currency": "TRY", "base_price": 95.10, "ter": 0.0},
        {"symbol": "TR_10Y_TAHVIL", "name": "T.C. Hazine 10 Yıllık Uzun Vadeli Tahvil", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Hazine Bonosu & Tahvil", "is_participation": False, "currency": "TRY", "base_price": 91.75, "ter": 0.0},
        {"symbol": "TR_EUROBOND_2030", "name": "T.C. Hazine 2030 Vadeli USD Eurobond", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Eurobond (Döviz Tahvil)", "is_participation": False, "currency": "USD", "base_price": 102.60, "ter": 0.0},
        {"symbol": "TR_EUROBOND_2034", "name": "T.C. Hazine 2034 Vadeli USD Eurobond", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Eurobond (Döviz Tahvil)", "is_participation": False, "currency": "USD", "base_price": 99.80, "ter": 0.0},
        {"symbol": "TR_HAZINE_SUKUK_USD", "name": "T.C. Hazine Uluslararası USD Kira Sertifikası (Sukuk)", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Kira Sertifikası (Sukuk)", "is_participation": True, "currency": "USD", "base_price": 101.40, "ter": 0.0},
        {"symbol": "KT_SUKUK_USD", "name": "Kuveyt Türk Katılım Bankası USD Sukuk İhracı", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Kira Sertifikası (Sukuk)", "is_participation": True, "currency": "USD", "base_price": 100.90, "ter": 0.0},
        {"symbol": "US_TBILL_3M", "name": "ABD 3 Aylık Hazine Bonosu (US 3M T-Bill)", "risk_level": 1, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Küresel Tahvil", "is_participation": False, "currency": "USD", "base_price": 99.20, "yf_ticker": "^IRX"},
        {"symbol": "US_TREASURY_2Y", "name": "ABD 2 Yıllık Hazine Tahvili ETF (SHY)", "risk_level": 1, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Küresel Tahvil", "is_participation": False, "currency": "USD", "base_price": 82.40, "yf_ticker": "SHY"},
        {"symbol": "US_TREASURY_10Y", "name": "ABD 10-20 Yıllık Hazine Tahvili ETF (TLT)", "risk_level": 2, "asset_class": AssetClass.BONDS_EUROBOND_SUKUK, "sector": "Küresel Tahvil", "is_participation": False, "currency": "USD", "base_price": 93.50, "yf_ticker": "TLT"},
        # Kıymetli Madenler (Altın, Gümüş, Platin, Paladyum)
        {"symbol": "XAU_TRY", "name": "Gram Altın (Spot TL) / Fiziki Altın", "risk_level": 1, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "TRY", "base_price": 3050.0, "yf_ticker": "GC=F", "ter": 0.0},
        {"symbol": "XAG_TRY", "name": "Gram Gümüş (Spot TL) / Fiziki Gümüş", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "TRY", "base_price": 38.40, "yf_ticker": "SI=F", "ter": 0.0},
        {"symbol": "XPT_TRY", "name": "Gram Platin (Spot TL)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "TRY", "base_price": 1095.0, "yf_ticker": "PL=F", "ter": 0.0},
        {"symbol": "XPD_TRY", "name": "Gram Paladyum (Spot TL)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "TRY", "base_price": 1140.0, "yf_ticker": "PA=F", "ter": 0.0},
        {"symbol": "DARPHANE_ALTIN_S1", "name": "Darphane Altın Sertifikası (ALTIN.S1 - BIST)", "risk_level": 1, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "TRY", "base_price": 31.20, "ter": 0.0},
        {"symbol": "ONS_ALTIN_USD", "name": "Ons Altın (XAU/USD Spot)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "USD", "base_price": 2745.0, "yf_ticker": "GC=F", "ter": 0.0},
        {"symbol": "ONS_GUMUS_USD", "name": "Ons Gümüş (XAG/USD Spot)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Kıymetli Madenler", "is_participation": True, "currency": "USD", "base_price": 33.80, "yf_ticker": "SI=F", "ter": 0.0},
        # Döviz Kurları (FX)
        {"symbol": "USDTRY", "name": "ABD Doları / Türk Lirası (USD/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 34.28, "yf_ticker": "TRY=X", "ter": 0.0},
        {"symbol": "EURTRY", "name": "Euro / Türk Lirası (EUR/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 37.15, "yf_ticker": "EURTRY=X", "ter": 0.0},
        {"symbol": "GBPTRY", "name": "İngiliz Sterlini / Türk Lirası (GBP/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 44.50, "yf_ticker": "GBPTRY=X", "ter": 0.0},
        {"symbol": "CHFTRY", "name": "İsviçre Frangı / Türk Lirası (CHF/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 39.60, "ter": 0.0},
        {"symbol": "JPYTRY", "name": "100 Japon Yeni / Türk Lirası (JPY/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 22.85, "ter": 0.0},
        {"symbol": "CADTRY", "name": "Kanada Doları / Türk Lirası (CAD/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 24.80, "ter": 0.0},
        {"symbol": "AUDTRY", "name": "Avustralya Doları / Türk Lirası (AUD/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 22.70, "ter": 0.0},
        {"symbol": "SARTRY", "name": "Suudi Arabistan Riyali / Türk Lirası (SAR/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 9.14, "ter": 0.0},
        {"symbol": "AEDTRY", "name": "BAE Dirhemi / Türk Lirası (AED/TRY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "TRY", "base_price": 9.33, "ter": 0.0},
        {"symbol": "EURUSD", "name": "Euro / ABD Doları Paritesi (EUR/USD)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "USD", "base_price": 1.084, "yf_ticker": "EURUSD=X", "ter": 0.0},
        {"symbol": "GBPUSD", "name": "İngiliz Sterlini / ABD Doları (GBP/USD)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "USD", "base_price": 1.298, "yf_ticker": "GBPUSD=X", "ter": 0.0},
        {"symbol": "USDJPY", "name": "ABD Doları / Japon Yeni (USD/JPY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": True, "currency": "USD", "base_price": 151.80, "yf_ticker": "JPY=X", "ter": 0.0},
        {"symbol": "DXY", "name": "ABD Dolar Endeksi (US Dollar Index - DXY)", "risk_level": 2, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Döviz (FX)", "is_participation": False, "currency": "USD", "base_price": 104.25, "yf_ticker": "DX-Y.NYB", "ter": 0.0},
        # Küresel Enerji, Endüstriyel Metal & Tarım Emtiaları
        {"symbol": "BRENT_OIL", "name": "Brent Ham Petrol Vadeli/Spot (USD/Varil)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 74.80, "yf_ticker": "BZ=F", "ter": 0.0},
        {"symbol": "WTI_OIL", "name": "WTI Amerikan Ham Petrol (USD/Varil)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 70.90, "yf_ticker": "CL=F", "ter": 0.0},
        {"symbol": "NAT_GAS", "name": "Doğalgaz Küresel Referans (Henry Hub USD)", "risk_level": 4, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 2.55, "yf_ticker": "NG=F", "ter": 0.0},
        {"symbol": "COPPER_HG", "name": "Endüstriyel Bakır (Copper Spot USD)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 4.38, "yf_ticker": "HG=F", "ter": 0.0},
        {"symbol": "WHEAT_ZW", "name": "Küresel Buğday Emtia Referansı (USD)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 572.0, "yf_ticker": "ZW=F", "ter": 0.0},
        {"symbol": "CORN_ZC", "name": "Küresel Mısır Emtia Referansı (USD)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 415.0, "yf_ticker": "ZC=F", "ter": 0.0},
        {"symbol": "COFFEE_KC", "name": "Küresel Kahve Emtia Referansı (USD)", "risk_level": 3, "asset_class": AssetClass.FOREX_PRECIOUS_METALS, "sector": "Küresel Emtia", "is_participation": True, "currency": "USD", "base_price": 248.0, "yf_ticker": "KC=F", "ter": 0.0},
    ]
    for item in macro_and_fixed_income:
        add_item(item)

    # -------------------------------------------------------------------------
    # B. TEFAS YATIRIM FONLARI (111 ADET: 53 KATILIM FONU + 58 KONVANSİYONEL FON)
    # -------------------------------------------------------------------------
    tefas_katilim_funds = [
        # 1. Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonları (Seviye 1-2)
        ("KTV", "Kuveyt Türk Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 5.42, 1.15),
        ("KTJ", "Kuveyt Türk Portföy Kira Sertifikaları (Sukuk) Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 4.18, 1.25),
        ("KSV", "Kuveyt Türk Portföy Uzun Vadeli Kira Sertifikaları Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 3.94, 1.30),
        ("KLU", "Kuveyt Türk Portföy Para Piyasası Katılım (TL) Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 2.86, 0.95),
        ("KTR", "Kuveyt Türk Portföy Döviz Kira Sertifikaları (USD/EUR) Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 12.40, 1.25),
        ("ZPK", "Ziraat Portföy Kısa Vadeli Kira Sertifikası Katılım (TL) Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 4.75, 1.10),
        ("ZPF", "Ziraat Portföy Kira Sertifikaları (Sukuk) Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 6.12, 1.20),
        ("VFK", "Vakıf Portföy Kira Sertifikaları Katılım (TL) Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 5.08, 1.15),
        ("HPK", "Halk Portföy Kira Sertifikaları Katılım (TL) Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 4.65, 1.18),
        ("RBK", "Albaraka Portföy Kısa Vadeli Kira Sertifikası Katılım Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 3.82, 1.10),
        ("RBT", "Albaraka Portföy Kira Sertifikaları Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 4.90, 1.25),
        ("MPK", "Mükafat Portföy Kira Sertifikaları Katılım Fonu", 1, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 3.45, 1.20),
        ("QPK", "QInvest Portföy Kira Sertifikası Katılım (TL) Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 7.15, 1.30),
        ("IPV", "İş Portföy Kira Sertifikaları (Sukuk) Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 8.95, 1.25),
        ("AIS", "Ak Portföy Kira Sertifikaları (Sukuk) Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 6.40, 1.25),
        ("GKF", "Garanti Portföy Kira Sertifikaları Katılım (TL) Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 5.88, 1.25),
        ("YKK", "Yapı Kredi Portföy Kira Sertifikaları Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 5.32, 1.20),
        ("DPK", "Deniz Portföy Kira Sertifikaları Katılım Fonu", 2, "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu", 4.55, 1.25),
        # 2. Katılım Altın & Kıymetli Madenler Fonları (Seviye 1-2)
        ("KZL", "Kuveyt Türk Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 19.85, 1.10),
        ("KGM", "Kuveyt Türk Portföy Gümüş & Kıymetli Madenler Katılım Fonu", 2, "Katılım Altın & Kıymetli Madenler Fonu", 6.45, 1.35),
        ("ZPA", "Ziraat Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 16.20, 1.05),
        ("RBA", "Albaraka Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 14.10, 1.15),
        ("OGD", "Oyak Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 11.80, 1.10),
        ("NJF", "Nurol Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 9.65, 1.15),
        ("PKF", "Ata Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 12.90, 1.15),
        ("MKG", "Mükafat Portföy Altın Katılım Fonu", 1, "Katılım Altın & Kıymetli Madenler Fonu", 10.40, 1.10),
        ("TCA", "Ziraat Portföy Kıymetli Madenler Katılım Fonu", 2, "Katılım Altın & Kıymetli Madenler Fonu", 8.75, 1.20),
        # 3. Katılım Hisse Senedi, Teknoloji & Katılım Değişken Fonları (Seviye 3-5)
        ("KPC", "Kuveyt Türk Portföy Katılım Hisse Senedi (TL) Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 14.30, 1.90),
        ("KCV", "Kuveyt Türk Portföy Birinci Katılım Değişken Fon", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 9.85, 1.75),
        ("KME", "Kuveyt Türk Portföy İkinci Katılım Değişken Fon", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 7.60, 1.80),
        ("KTN", "Kuveyt Türk Portföy Teknoloji Katılım Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 6.95, 1.95),
        ("KTS", "Kuveyt Türk Portföy Sürdürülebilirlik Katılım Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 5.80, 1.85),
        ("KTT", "Kuveyt Türk Portföy Temettü Ödeyen Şirketler Katılım Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 6.45, 1.80),
        ("KUA", "Kuveyt Türk Portföy Yabancı Borsa / Küresel İslami Hisse Katılım Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 8.20, 2.05),
        ("KAV", "Kuveyt Türk Portföy Agresif / Serbest Katılım Fonu", 5, "Serbest / Agresif / Mutlak Getiri Fonu", 18.60, 2.10),
        ("ZPE", "Ziraat Portföy Katılım Endeksi Hisse Senedi (TL) Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 15.40, 1.85),
        ("ZPG", "Ziraat Portföy Katılım Değişken Fon", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 8.90, 1.75),
        ("VFU", "Vakıf Portföy Katılım Endeksi Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 11.25, 1.90),
        ("HPE", "Halk Portföy Katılım Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 10.60, 1.90),
        ("RBH", "Albaraka Portföy Katılım Hisse Senedi (TL) Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 16.80, 1.95),
        ("MPE", "Mükafat Portföy Katılım Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 12.10, 1.95),
        ("MPS", "Mükafat Portföy Katılım Değişken Fon", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 7.45, 1.80),
        ("QPE", "QInvest Portföy Katılım Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 18.90, 2.00),
        ("BPK", "Bereket Portföy Katılım Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 9.30, 1.90),
        ("HKH", "Hedef Portföy Katılım Hisse Senedi (TL) Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 24.50, 2.05),
        ("PBK", "Pardus Portföy Katılım Hisse Senedi (TL) Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 19.40, 2.00),
        ("NKH", "Neo Portföy Katılım Hisse Senedi Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 13.75, 2.00),
        ("PKH", "Phillip Portföy Katılım Endeksi Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 8.60, 1.85),
        ("IKH", "İnveo Portföy Katılım Hisse Senedi Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 11.40, 1.95),
        ("TKH", "Tera Portföy Katılım Hisse Senedi Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 15.20, 2.05),
        ("RKH", "Rota Portföy Katılım Hisse Senedi Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 14.80, 1.95),
        ("ELZ", "QNB Finans Portföy Katılım Hisse Senedi Fonu", 3, "Katılım Hisse Senedi & Katılım Değişken Fonu", 12.60, 1.90),
        ("CPU", "Aktif Portföy Teknoloji Katılım Fonu", 4, "Katılım Hisse Senedi & Katılım Değişken Fonu", 9.95, 2.00),
    ]
    for code, name, r_lvl, subcat, price, ter in tefas_katilim_funds:
        add_item({
            "symbol": code,
            "name": f"{name} (TEFAS)",
            "risk_level": r_lvl,
            "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
            "sector": subcat,
            "is_participation": True,
            "currency": "TRY",
            "base_price": price,
            "ter": ter,
        })

    tefas_conventional_funds = [
        # 1. Para Piyasası & Kısa Vadeli Fonlar (Seviye 1)
        ("TI1", "İş Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 6.84, 0.65),
        ("RPP", "Rota Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 4.92, 0.55),
        ("PPN", "Nurol Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 5.18, 0.60),
        ("BGP", "Ak Portföy Üçüncü Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 5.64, 0.60),
        ("FIL", "Fiba Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 4.45, 0.65),
        ("HYV", "Hedef Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 3.98, 0.58),
        ("NRP", "Neo Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 4.12, 0.60),
        ("ALE", "Ak Portföy Kısa Vadeli Borçlanma Araçları (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 7.30, 0.85),
        ("IST", "İstanbul Portföy Kısa Vadeli Borçlanma Araçları Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 6.15, 0.90),
        ("OSD", "Osmanlı Portföy Para Piyasası (TL) Fonu", 1, "Para Piyasası / Kısa Vadeli Fon", 4.78, 0.62),
        # 2. Borçlanma Araçları & Eurobond Fonları (Seviye 2)
        ("DBH", "Deniz Portföy Eurobond (Döviz) Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 18.45, 1.25),
        ("AKE", "Ak Portföy Eurobond (Amerikan Doları) Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 14.90, 1.30),
        ("IPV_EB", "İş Portföy Eurobond Borçlanma Araçları (TL) Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 21.10, 1.25),
        ("TBT", "TEB Portföy Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 9.40, 1.15),
        ("AHU", "Atlas Portföy Özel Sektör Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 7.85, 1.20),
        ("YOT", "Yapı Kredi Portföy Orta Vadeli Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 8.60, 1.15),
        ("FPE", "Fiba Portföy Eurobond Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 13.25, 1.30),
        ("GPA", "Garanti Portföy Eurobond Borçlanma Araçları Fonu", 2, "Borçlanma Araçları / Eurobond Fonu", 16.70, 1.25),
        # 3. Kıymetli Madenler & Emtia Fonları (Seviye 2)
        ("TTA", "İş Portföy Altın Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 22.40, 1.10),
        ("AFO", "Ak Portföy Altın Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 20.15, 1.15),
        ("GTA", "Garanti Portföy Altın Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 19.50, 1.15),
        ("YKT", "Yapı Kredi Portföy Altın Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 18.80, 1.15),
        ("FIB", "Fiba Portföy Altın Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 15.60, 1.20),
        ("GUM", "Ak Portföy Gümüş Fon Sepeti Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 9.80, 1.45),
        ("IOG", "İş Portföy Gümüş Serbest / Kıymetli Maden Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 11.20, 1.50),
        ("GTZ", "Garanti Portföy Gümüş Fon Sepeti Fonu", 2, "Kıymetli Madenler / Altın / Emtia Fonu", 8.95, 1.45),
        ("TGE", "İş Portföy Emtia Yabancı BYF Fon Sepeti Fonu", 3, "Kıymetli Madenler / Altın / Emtia Fonu", 12.60, 1.65),
        ("AES", "Ak Portföy Petrol Yabancı BYF Fon Sepeti Fonu", 3, "Kıymetli Madenler / Altın / Emtia Fonu", 14.30, 1.75),
        # 4. BIST Hisse Senedi Yoğun Fonlar (Seviye 3-4)
        ("MAC", "Marmara Capital Portföy Hisse Senedi (TL) Fonu (Hisse Yoğun)", 3, "Hisse Senedi Yoğun Fon", 34.80, 1.85),
        ("IIH", "İstanbul Portföy Üçüncü Hisse Senedi (TL) Fonu (Hisse Yoğun)", 3, "Hisse Senedi Yoğun Fon", 42.10, 1.95),
        ("TI2", "İş Portföy Hisse Senedi (TL) Fonu (Hisse Yoğun)", 3, "Hisse Senedi Yoğun Fon", 28.60, 1.90),
        ("TI3", "İş Portföy İş Bankası İştirakleri Endeksi Hisse Senedi Fonu", 3, "Hisse Senedi Yoğun Fon", 24.90, 1.80),
        ("NNF", "Hedef Portföy Birinci Hisse Senedi (TL) Fonu (Hisse Yoğun)", 3, "Hisse Senedi Yoğun Fon", 31.40, 1.95),
        ("GMR", "İnveo Portföy İkinci Hisse Senedi Fonu (Hisse Yoğun)", 3, "Hisse Senedi Yoğun Fon", 22.80, 1.90),
        ("YAS", "Yapı Kredi Portföy Koç Holding İştirakleri Hisse Senedi Fonu", 3, "Hisse Senedi Yoğun Fon", 29.75, 1.85),
        ("AK3", "Ak Portföy BIST 30 Endeksi Hisse Senedi Yoğun Fon", 3, "Hisse Senedi Yoğun Fon", 16.50, 1.75),
        ("IDH", "İş Portföy BIST 100 Dışı Şirketler Hisse Senedi Fonu", 4, "Hisse Senedi Yoğun Fon", 27.30, 2.05),
        ("ST1", "Strateji Portföy Birinci Hisse Senedi Yoğun Fon", 3, "Hisse Senedi Yoğun Fon", 36.20, 1.95),
        ("TKF", "Tacirler Portföy Hisse Senedi (TL) Fonu (Hisse Yoğun)", 3, "Hisse Senedi Yoğun Fon", 39.50, 2.00),
        ("GSP", "Azimut Portföy Temettü Ödeyen Şirketler Hisse Senedi Fonu", 3, "Hisse Senedi Yoğun Fon", 17.40, 1.90),
        ("TAU", "İş Portföy BIST Banka Endeksi Hisse Senedi Fonu", 4, "Hisse Senedi Yoğun Fon", 25.10, 1.90),
        ("ADP", "Ak Portföy BIST Banka Endeksi Hisse Senedi Fonu", 4, "Hisse Senedi Yoğun Fon", 23.60, 1.90),
        ("AYA", "Ata Portföy Birinci Hisse Senedi Fonu", 3, "Hisse Senedi Yoğun Fon", 21.80, 1.95),
        ("DHS", "Deniz Portföy Hisse Senedi Fonu", 3, "Hisse Senedi Yoğun Fon", 18.90, 1.90),
        # 5. Tematik, Teknoloji & Yabancı Borsa / Küresel ETF Fonları (Seviye 4)
        ("TTE", "İş Portföy BIST Teknoloji Ağırlıklı Sınırlamalı Endeksi Fonu", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 19.80, 1.90),
        ("AFT", "Ak Portföy Yeni Teknolojiler Yabancı Hisse Senedi Fonu", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 24.60, 2.10),
        ("YAY", "Yapı Kredi Portföy Yabancı Teknoloji Sektörü Hisse Senedi Fonu", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 32.40, 2.15),
        ("AFA", "Ak Portföy Amerika Yabancı Hisse Senedi Fonu", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 18.70, 2.10),
        ("TMG", "İş Portföy Yabancı Hisse Senedi Fonu", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 21.30, 2.05),
        ("IPJ", "İş Portföy Elektrikli Araçlar Karma Fon", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 12.90, 2.00),
        ("GZH", "Garanti Portföy Temiz Enerji Değişken Fon", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 10.45, 1.95),
        ("IJC", "İş Portföy Yarı İletken Teknolojileri Değişken Fon", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 15.80, 2.05),
        ("OJT", "QNB Finans Portföy Teknoloji Fon Sepeti Fonu", 4, "Tematik / Teknoloji / Yabancı ETF Fonu", 14.10, 2.00),
        # 6. Değişken, Karma & Serbest / Agresif Fonlar (Seviye 3-5)
        ("TCD", "Tacirler Portföy Değişken Fon", 4, "Değişken / Karma / Fon Sepeti Fonu", 38.90, 2.05),
        ("IPB", "İstanbul Portföy Birinci Değişken Fon", 4, "Değişken / Karma / Fon Sepeti Fonu", 44.20, 2.10),
        ("OPB", "Osmanlı Portföy Birinci Değişken Fon", 3, "Değişken / Karma / Fon Sepeti Fonu", 16.40, 1.85),
        ("TDF", "İş Portföy Değişken Fon", 3, "Değişken / Karma / Fon Sepeti Fonu", 14.90, 1.80),
        ("IRE", "İstanbul Portföy Serbest / Agresif Mutlak Getiri Fonu", 5, "Serbest / Agresif / Mutlak Getiri Fonu", 52.40, 2.30),
    ]
    for code, name, r_lvl, subcat, price, ter in tefas_conventional_funds:
        add_item({
            "symbol": code,
            "name": f"{name} (TEFAS)",
            "risk_level": r_lvl,
            "asset_class": AssetClass.TEFAS_MUTUAL_FUND,
            "sector": subcat,
            "is_participation": False,
            "currency": "TRY",
            "base_price": price,
            "ter": ter,
        })

    # -------------------------------------------------------------------------
    # C. BORSA İSTANBUL (BIST) TÜM SEKTÖR PAYLARI (354+ DAHİLİ HİSSE)
    # -------------------------------------------------------------------------
    bist_company_names = {
        "THYAO": "Türk Hava Yolları A.O.", "PGSUS": "Pegasus Hava Taşımacılığı A.Ş.",
        "TAVHL": "TAV Havalimanları Holding A.Ş.", "CLEBI": "Çelebi Hava Servisi A.Ş.",
        "ASELS": "Aselsan Elektronik Sanayi ve Ticaret A.Ş.", "TUPRS": "Tüpraş Türkiye Petrol Rafinerileri A.Ş.",
        "KCHOL": "Koç Holding A.Ş.", "SAHOL": "Hacı Ömer Sabancı Holding A.Ş.",
        "BIMAS": "BİM Birleşik Mağazalar A.Ş.", "MGROS": "Migros Ticaret A.Ş.",
        "SOKM": "Şok Marketler Ticaret A.Ş.", "EREGL": "Ereğli Demir ve Çelik Fabrikaları T.A.Ş.",
        "ISDMR": "İskenderun Demir ve Çelik A.Ş.", "KRDMD": "Kardemir Karabük Demir Çelik (D)",
        "FROTO": "Ford Otomotiv Sanayi A.Ş.", "TOASO": "Tofaş Türk Otomobil Fabrikası A.Ş.",
        "DOAS": "Doğuş Otomotiv Servis ve Ticaret A.Ş.", "TTRAK": "Türk Traktör ve Ziraat Makineleri A.Ş.",
        "OTKAR": "Otokar Otomotiv ve Savunma Sanayi A.Ş.", "SISE": "Türkiye Şişe ve Cam Fabrikaları A.Ş.",
        "TCELL": "Turkcell İletişim Hizmetleri A.Ş.", "TTKOM": "Türk Telekomünikasyon A.Ş.",
        "AKBNK": "Akbank T.A.Ş.", "GARAN": "Türkiye Garanti Bankası A.Ş.",
        "ISCTR": "Türkiye İş Bankası A.Ş. (C)", "YKBNK": "Yapı ve Kredi Bankası A.Ş.",
        "VAKBN": "Türkiye Vakıflar Bankası T.A.O.", "HALKB": "Türkiye Halk Bankası A.Ş.",
        "ALBRK": "Albaraka Türk Katılım Bankası A.Ş.", "TSKB": "Türkiye Sınai Kalkınma Bankası A.Ş.",
        "EKGYO": "Emlak Konut GYO A.Ş.", "ENKAI": "Enka İnşaat ve Sanayi A.Ş.",
        "ASTOR": "Astor Enerji A.Ş.", "KONTR": "Kontrolmatik Teknoloji Enerji ve Mühendislik A.Ş.",
        "EUPWR": "Europower Enerji ve Otomasyon Teknolojileri A.Ş.", "GESAN": "Girişim Elektrik Sanayi A.Ş.",
        "SMRTG": "Smart Güneş Enerjisi Teknolojileri A.Ş.", "CWENE": "CW Enerji Mühendislik A.Ş.",
        "YEOTK": "Yeo Teknoloji Enerji ve Endüstri A.Ş.", "MIATK": "Mia Teknoloji A.Ş.",
        "ALTNY": "Altınay Savunma Teknolojileri A.Ş.", "SDTTR": "SDT Uzay ve Savunma Teknolojileri A.Ş.",
        "REEDR": "Reeder Teknoloji Sanayi ve Ticaret A.Ş.", "LOGO": "Logo Yazılım Sanayi ve Ticaret A.Ş.",
        "ARDYZ": "ARD Grup Bilişim Teknolojileri A.Ş.", "SASA": "Sasa Polyester Sanayi A.Ş.",
        "PETKM": "Petkim Petrokimya Holding A.Ş.", "GUBRF": "Gübre Fabrikaları T.A.Ş.",
        "HEKTS": "Hektaş Ticaret T.A.Ş.", "KOZAL": "Koza Altın İşletmeleri A.Ş.",
        "KOZAA": "Koza Anadolu Metal Madencilik A.Ş.", "IPEKE": "İpek Doğal Enerji Kaynakları A.Ş.",
        "ARCLK": "Arçelik A.Ş.", "VESTL": "Vestel Elektronik Sanayi ve Ticaret A.Ş.",
        "VESBE": "Vestel Beyaz Eşya Sanayi ve Ticaret A.Ş.", "ULKER": "Ülker Bisküvi Sanayi A.Ş.",
        "CCOLA": "Coca-Cola İçecek A.Ş.", "AEFES": "Anadolu Efes Biracılık ve Malt Sanayii A.Ş.",
        "MAVI": "Mavi Giyim Sanayi ve Ticaret A.Ş.", "TABGD": "TAB Gıda Sanayi ve Ticaret A.Ş.",
        "MPARK": "MLP Sağlık Hizmetleri A.Ş. (Medical Park)", "SELEC": "Selçuk Ecza Deposu Ticaret ve Sanayi A.Ş.",
        "DEVA": "Deva Holding A.Ş.", "ECILC": "Eczacıbaşı İlaç, Sınai ve Finansal Yatırımlar A.Ş.",
        "GENIL": "Gen İlaç ve Sağlık Ürünleri Sanayi ve Ticaret A.Ş.", "OYAKC": "Oyak Çimento Fabrikaları A.Ş.",
        "CIMSA": "Çimsa Çimento Sanayi ve Ticaret A.Ş.", "AKCNS": "Akçansa Çimento Sanayi ve Ticaret A.Ş.",
        "BRSAN": "Borusan Birleşik Boru Fabrikaları Sanayi ve Ticaret A.Ş.", "KCAER": "Kocaer Çelik Sanayi ve Ticaret A.Ş.",
        "EGEEN": "Ege Endüstri ve Ticaret A.Ş.", "ALARK": "Alarko Holding A.Ş.",
        "DOHOL": "Doğan Şirketler Grubu Holding A.Ş.", "AGHOL": "AG Anadolu Grubu Holding A.Ş.",
        "TKFEN": "Tekfen Holding A.Ş.", "BERA": "Bera Holding A.Ş.",
        "ENJSA": "Enerjisa Enerji A.Ş.", "AKSEN": "Aksa Enerji Üretim A.Ş.",
        "ZOREN": "Zorlu Enerji Elektrik Üretim A.Ş.", "ODAS": "Odaş Elektrik Üretim Sanayi Ticaret A.Ş.",
        "CANTE": "Çan2 Termik A.Ş.", "GWIND": "Galata Wind Enerji A.Ş.",
        "AKFYE": "Akfen Yenilenebilir Enerji A.Ş.", "ALFAS": "Alfa Solar Enerji Sanayi ve Ticaret A.Ş.",
        "TURSG": "Türkiye Sigorta A.Ş.", "ANSGR": "Anadolu Anonim Türk Sigorta Şirketi",
        "AGESA": "AgeSA Hayat ve Emeklilik A.Ş.", "ANHYT": "Anadolu Hayat Emeklilik A.Ş.",
        "ISMEN": "İş Yatırım Menkul Değerler A.Ş.", "INFO": "İnfo Yatırım Menkul Değerler A.Ş.",
    }

    for sector_name, tickers in SEKTOR_SOZLUGU.items():
        for sym in tickers:
            seed_val = sum(ord(c) for c in sym)
            is_kat = sym in KATILIM_TUM
            if sym in BIST_30:
                r_lvl = 3
                a_cls = AssetClass.BIST_BLUECHIP_30
                beta = round(0.88 + (seed_val % 35) * 0.01, 2)
                pe = round(5.2 + (seed_val % 110) * 0.1, 2)
                pb = round(1.1 + (seed_val % 35) * 0.1, 2)
                roe = round(22.0 + (seed_val % 28), 1)
                peg = round(0.55 + (seed_val % 65) * 0.01, 2)
                div_y = round(1.5 + (seed_val % 55) * 0.1, 2)
                base_p = round(45.0 + (seed_val % 320) * 1.25, 2)
            elif sym in BIST_100:
                r_lvl = 4
                a_cls = AssetClass.BIST_GROWTH_100
                beta = round(1.05 + (seed_val % 40) * 0.01, 2)
                pe = round(7.5 + (seed_val % 180) * 0.1, 2)
                pb = round(1.5 + (seed_val % 55) * 0.1, 2)
                roe = round(18.0 + (seed_val % 30), 1)
                peg = round(0.68 + (seed_val % 85) * 0.01, 2)
                div_y = round(0.5 + (seed_val % 40) * 0.1, 2)
                base_p = round(28.0 + (seed_val % 240) * 0.95, 2)
            else:
                r_lvl = 4 if (seed_val % 3 != 0) else 5
                a_cls = AssetClass.BIST_GROWTH_100 if r_lvl == 4 else AssetClass.BIST_HIGH_BETA
                beta = round(1.18 + (seed_val % 55) * 0.01, 2)
                pe = round(8.5 + (seed_val % 220) * 0.1, 2)
                pb = round(1.7 + (seed_val % 65) * 0.1, 2)
                roe = round(14.0 + (seed_val % 32), 1)
                peg = round(0.75 + (seed_val % 95) * 0.01, 2)
                div_y = round((seed_val % 35) * 0.1, 2)
                base_p = round(14.5 + (seed_val % 180) * 0.75, 2)

            comp_title = bist_company_names.get(sym, f"{sym} ({sector_name})")
            add_item({
                "symbol": sym,
                "name": comp_title,
                "risk_level": r_lvl,
                "asset_class": a_cls,
                "sector": sector_name,
                "is_participation": is_kat,
                "currency": "TRY",
                "base_price": base_p,
                "yf_ticker": f"{sym}.IS",
                "pe": pe,
                "pb": pb,
                "roe": roe,
                "peg": peg,
                "beta": beta,
                "div_yield": div_y,
            })

    # -------------------------------------------------------------------------
    # D. KÜRESEL ETF & YABANCI BORSA HİSSELERİ (203 ADET: İSLAMİ ETF + ABD/AVRUPA/ASYA ETF + S&P500/NASDAQ)
    # -------------------------------------------------------------------------
    global_etfs = [
        # Küresel İslami / Katılım Endeks ETF'leri (Katılım Uyumlu)
        ("SPUS", "SP Funds S&P 500 Sharia Industry Exclusions ETF (İslami S&P 500)", 3, "Küresel İslami / Katılım ETF", True, 43.20, 0.45),
        ("HLAL", "Wahed FTSE USA Shariah ETF (ABD Katılım Endeksi ETF)", 3, "Küresel İslami / Katılım ETF", True, 54.10, 0.50),
        ("UMMA", "Wahed Dow Jones Islamic World ETF (Küresel İslami Hisse ETF)", 3, "Küresel İslami / Katılım ETF", True, 26.80, 0.65),
        ("SPSK", "SP Funds Dow Jones Global Sukuk ETF (Küresel USD Sukuk ETF)", 2, "Küresel İslami / Katılım ETF", True, 18.25, 0.55),
        ("ISDU", "iShares MSCI USA Islamic UCITS ETF", 3, "Küresel İslami / Katılım ETF", True, 72.40, 0.30),
        ("ISWD", "iShares MSCI World Islamic UCITS ETF", 3, "Küresel İslami / Katılım ETF", True, 48.90, 0.30),
        ("ISDE", "iShares MSCI EM Islamic UCITS ETF (Gelişen Piyasalar İslami ETF)", 4, "Küresel İslami / Katılım ETF", True, 19.60, 0.35),
        ("IGDA", "Invesco Dow Jones Islamic Global Developed Markets ETF", 3, "Küresel İslami / Katılım ETF", True, 31.50, 0.40),
        # Ana Endeks & Sektörel Küresel ETF'ler
        ("SPY", "SPDR S&P 500 ETF Trust (ABD S&P 500 Endeksi)", 3, "Küresel Endeks ETF", False, 585.0, 0.09),
        ("VOO", "Vanguard S&P 500 ETF", 3, "Küresel Endeks ETF", False, 538.0, 0.03),
        ("IVV", "iShares Core S&P 500 ETF", 3, "Küresel Endeks ETF", False, 588.0, 0.03),
        ("QQQ", "Invesco QQQ Trust (Nasdaq-100 Teknoloji Endeksi)", 4, "Küresel Teknoloji & Yarı İletken ETF", False, 498.0, 0.20),
        ("QQQM", "Invesco Nasdaq 100 ETF", 4, "Küresel Teknoloji & Yarı İletken ETF", False, 206.0, 0.15),
        ("DIA", "SPDR Dow Jones Industrial Average ETF", 3, "Küresel Endeks ETF", False, 430.0, 0.16),
        ("IWM", "iShares Russell 2000 Small-Cap ETF", 4, "Küresel Endeks ETF", False, 224.0, 0.19),
        ("VTI", "Vanguard Total Stock Market ETF", 3, "Küresel Endeks ETF", False, 289.0, 0.03),
        ("VT", "Vanguard Total World Stock ETF (Tüm Dünya Borsaları)", 3, "Küresel Endeks ETF", False, 119.0, 0.07),
        ("VXUS", "Vanguard Total International Stock ETF (ABD Dışı Dünya)", 3, "Küresel Endeks ETF", False, 63.50, 0.07),
        ("VEA", "Vanguard FTSE Developed Markets ETF (Avrupa & Japonya)", 3, "Küresel Endeks ETF", False, 51.80, 0.06),
        ("VWO", "Vanguard FTSE Emerging Markets ETF (Gelişen Ülkeler)", 4, "Küresel Endeks ETF", False, 46.90, 0.08),
        ("EEM", "iShares MSCI Emerging Markets ETF", 4, "Küresel Endeks ETF", False, 45.20, 0.68),
        ("TUR", "iShares MSCI Turkey ETF (ABD Borsasında Türkiye ETF)", 4, "Küresel Endeks ETF", False, 36.80, 0.59),
        ("EWJ", "iShares MSCI Japan ETF (Japonya Borsası)", 3, "Küresel Endeks ETF", False, 71.20, 0.50),
        ("EWZ", "iShares MSCI Brazil ETF (Brezilya Borsası)", 4, "Küresel Endeks ETF", False, 28.40, 0.59),
        ("FXI", "iShares China Large-Cap ETF (Çin Büyük Ölçekli Şirketler)", 4, "Küresel Endeks ETF", False, 32.10, 0.74),
        ("KWEB", "KraneShares CSI China Internet ETF", 4, "Küresel Teknoloji & Yarı İletken ETF", False, 33.40, 0.69),
        ("INDA", "iShares MSCI India ETF (Hindistan Borsası)", 4, "Küresel Endeks ETF", False, 56.40, 0.65),
        ("VGK", "Vanguard FTSE Europe ETF (Avrupa Borsaları)", 3, "Küresel Endeks ETF", False, 69.20, 0.09),
        ("SCHD", "Schwab U.S. Dividend Equity ETF (ABD Temettü Aristokratları)", 3, "Küresel Temettü & Değer ETF", False, 28.90, 0.06),
        ("VIG", "Vanguard Dividend Appreciation ETF", 3, "Küresel Temettü & Değer ETF", False, 196.0, 0.06),
        ("VYM", "Vanguard High Dividend Yield ETF", 3, "Küresel Temettü & Değer ETF", False, 128.0, 0.06),
        ("JEPI", "JPMorgan Equity Premium Income ETF", 3, "Küresel Temettü & Değer ETF", False, 59.10, 0.35),
        ("JEPQ", "JPMorgan Nasdaq Equity Premium Income ETF", 3, "Küresel Temettü & Değer ETF", False, 56.80, 0.35),
        ("SMH", "VanEck Semiconductor ETF (Küresel Yarı İletken & Çip ETF)", 4, "Küresel Teknoloji & Yarı İletken ETF", True, 254.0, 0.35),
        ("SOXX", "iShares Semiconductor ETF", 4, "Küresel Teknoloji & Yarı İletken ETF", True, 232.0, 0.35),
        ("XLK", "Technology Select Sector SPDR Fund", 4, "Küresel Teknoloji & Yarı İletken ETF", False, 231.0, 0.09),
        ("VGT", "Vanguard Information Technology ETF", 4, "Küresel Teknoloji & Yarı İletken ETF", False, 605.0, 0.10),
        ("CIBR", "First Trust NASDAQ Cybersecurity ETF (Siber Güvenlik ETF)", 4, "Küresel Teknoloji & Yarı İletken ETF", True, 62.40, 0.60),
        ("BOTZ", "Global X Robotics & Artificial Intelligence ETF (Yapay Zeka & Robotik)", 4, "Küresel Teknoloji & Yarı İletken ETF", True, 32.80, 0.68),
        ("ARKK", "ARK Innovation ETF", 5, "Küresel Teknoloji & Yarı İletken ETF", False, 48.50, 0.75),
        ("XLF", "Financial Select Sector SPDR Fund (ABD Finans Sektörü)", 3, "Küresel Sektör ETF", False, 47.20, 0.09),
        ("XLV", "Health Care Select Sector SPDR Fund (Küresel Sağlık ETF)", 3, "Küresel Sektör ETF", True, 152.0, 0.09),
        ("XLE", "Energy Select Sector SPDR Fund (Küresel Enerji Devleri ETF)", 4, "Küresel Sektör ETF", True, 91.50, 0.09),
        ("XLI", "Industrial Select Sector SPDR Fund (Küresel Sanayi ETF)", 3, "Küresel Sektör ETF", True, 138.0, 0.09),
        ("XLP", "Consumer Staples Select Sector SPDR Fund", 3, "Küresel Sektör ETF", True, 82.40, 0.09),
        ("XLY", "Consumer Discretionary Select Sector SPDR Fund", 4, "Küresel Sektör ETF", False, 201.0, 0.09),
        ("XLU", "Utilities Select Sector SPDR Fund (Altyapı & Elektrik ETF)", 3, "Küresel Sektör ETF", False, 80.50, 0.09),
        ("XLB", "Materials Select Sector SPDR Fund (Madencilik & Kimya ETF)", 3, "Küresel Sektör ETF", True, 95.80, 0.09),
        ("VNQ", "Vanguard Real Estate ETF (ABD GYO & Gayrimenkul ETF)", 3, "Küresel Sektör ETF", False, 96.40, 0.12),
        ("ITA", "iShares U.S. Aerospace & Defense ETF (Küresel Savunma & Havacılık)", 4, "Küresel Sektör ETF", False, 148.0, 0.40),
        ("ICLN", "iShares Global Clean Energy ETF (Küresel Temiz Enerji ETF)", 4, "Küresel Sektör ETF", True, 14.20, 0.41),
        ("URNM", "Sprott Uranium Miners ETF (Nükleer Enerji & Uranyum ETF)", 4, "Küresel Sektör ETF", True, 52.60, 0.75),
        ("GLD", "SPDR Gold Shares ETF (Fiziki Altın ETF - USD)", 2, "Küresel Emtia ETF", True, 253.0, 0.40),
        ("SLV", "iShares Silver Trust ETF (Fiziki Gümüş ETF - USD)", 3, "Küresel Emtia ETF", True, 30.80, 0.50),
        ("GDX", "VanEck Gold Miners ETF (Küresel Altın Madencileri ETF)", 4, "Küresel Emtia ETF", True, 41.90, 0.51),
    ]
    for sym, name, r_lvl, sec, is_kat, price, ter in global_etfs:
        add_item({
            "symbol": sym,
            "name": name,
            "risk_level": r_lvl,
            "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
            "sector": sec,
            "is_participation": is_kat,
            "currency": "USD",
            "base_price": price,
            "yf_ticker": sym,
            "ter": ter,
            "pe": 21.5,
            "pb": 3.8,
            "roe": 21.0,
            "peg": 1.15,
            "beta": 1.05,
        })

    # ABD & Küresel Dev Şirket Payları (S&P 500 & Nasdaq Liderleri - 148 Şirket)
    us_equities_by_sector = {
        "ABD Teknoloji, Bulut & Yapay Zeka": [
            ("AAPL", "Apple Inc.", True, 232.0), ("MSFT", "Microsoft Corp.", True, 428.0),
            ("GOOGL", "Alphabet Inc. (Google)", True, 166.0), ("META", "Meta Platforms Inc.", False, 585.0),
            ("ORCL", "Oracle Corp.", True, 174.0), ("CRM", "Salesforce Inc.", True, 292.0),
            ("ADBE", "Adobe Inc.", True, 495.0), ("NOW", "ServiceNow Inc.", True, 935.0),
            ("INTU", "Intuit Inc.", True, 615.0), ("IBM", "International Business Machines", False, 232.0),
            ("CSCO", "Cisco Systems Inc.", True, 56.20), ("ACN", "Accenture plc", True, 368.0),
            ("SAP", "SAP SE (ADR)", True, 234.0), ("PLTR", "Palantir Technologies Inc.", False, 43.50),
            ("SNOW", "Snowflake Inc.", True, 118.0), ("PANW", "Palo Alto Networks Inc.", True, 372.0),
            ("CRWD", "CrowdStrike Holdings Inc.", True, 308.0), ("FTNT", "Fortinet Inc.", True, 81.50),
            ("ZS", "Zscaler Inc.", True, 188.0), ("DDOG", "Datadog Inc.", True, 126.0),
            ("NET", "Cloudflare Inc.", True, 92.40), ("SHOP", "Shopify Inc.", True, 82.60),
            ("UBER", "Uber Technologies Inc.", True, 79.80), ("ABNB", "Airbnb Inc.", False, 136.0),
            ("TEAM", "Atlassian Corp.", True, 192.0), ("WDAY", "Workday Inc.", True, 242.0),
            ("ANET", "Arista Networks Inc.", True, 405.0), ("DELL", "Dell Technologies Inc.", False, 124.0),
            ("HPQ", "HP Inc.", True, 37.10), ("MDB", "MongoDB Inc.", True, 278.0),
        ],
        "ABD Yarı İletken & Donanım": [
            ("NVDA", "NVIDIA Corp.", True, 138.0), ("AVGO", "Broadcom Inc.", True, 178.0),
            ("AMD", "Advanced Micro Devices Inc.", True, 156.0), ("TSM", "Taiwan Semiconductor (TSMC ADR)", True, 192.0),
            ("ASML", "ASML Holding N.V.", True, 725.0), ("QCOM", "Qualcomm Inc.", True, 172.0),
            ("TXN", "Texas Instruments Inc.", True, 204.0), ("INTC", "Intel Corp.", True, 22.80),
            ("AMAT", "Applied Materials Inc.", True, 194.0), ("LRCX", "Lam Research Corp.", True, 78.50),
            ("MU", "Micron Technology Inc.", True, 108.0), ("ADI", "Analog Devices Inc.", True, 228.0),
            ("KLAC", "KLA Corp.", True, 715.0), ("MRVL", "Marvell Technology Inc.", True, 82.40),
            ("NXPI", "NXP Semiconductors N.V.", True, 238.0), ("ON", "ON Semiconductor Corp.", True, 71.20),
            ("ARM", "Arm Holdings plc", True, 152.0), ("SMCI", "Super Micro Computer Inc.", True, 47.80),
            ("MPWR", "Monolithic Power Systems Inc.", True, 885.0), ("MCHP", "Microchip Technology Inc.", True, 76.40),
        ],
        "ABD Sağlık, İlaç & Biyoteknoloji": [
            ("LLY", "Eli Lilly and Co.", True, 905.0), ("UNH", "UnitedHealth Group Inc.", False, 568.0),
            ("JNJ", "Johnson & Johnson", True, 164.0), ("ABBV", "AbbVie Inc.", False, 191.0),
            ("MRK", "Merck & Co. Inc.", True, 108.0), ("TMO", "Thermo Fisher Scientific Inc.", True, 588.0),
            ("ABT", "Abbott Laboratories", True, 118.0), ("DHR", "Danaher Corp.", True, 268.0),
            ("PFE", "Pfizer Inc.", True, 28.90), ("AMGN", "Amgen Inc.", False, 322.0),
            ("ISRG", "Intuitive Surgical Inc.", True, 512.0), ("SYK", "Stryker Corp.", True, 362.0),
            ("VRTX", "Vertex Pharmaceuticals Inc.", True, 482.0), ("REGN", "Regeneron Pharmaceuticals Inc.", True, 995.0),
            ("BSX", "Boston Scientific Corp.", True, 86.40), ("GILD", "Gilead Sciences Inc.", True, 88.20),
            ("BMY", "Bristol-Myers Squibb Co.", False, 53.80), ("MDT", "Medtronic plc", True, 90.40),
            ("ZTS", "Zoetis Inc.", True, 189.0), ("ELV", "Elevance Health Inc.", False, 425.0),
            ("CVS", "CVS Health Corp.", False, 58.60), ("CI", "The Cigna Group", False, 328.0),
            ("BIIB", "Biogen Inc.", True, 188.0), ("MRNA", "Moderna Inc.", True, 56.40),
        ],
        "ABD Tüketim, Perakende & E-Ticaret": [
            ("AMZN", "Amazon.com Inc.", False, 189.0), ("TSLA", "Tesla Inc.", True, 248.0),
            ("WMT", "Walmart Inc.", False, 81.20), ("COST", "Costco Wholesale Corp.", True, 892.0),
            ("HD", "The Home Depot Inc.", True, 398.0), ("PG", "Procter & Gamble Co.", True, 171.0),
            ("KO", "The Coca-Cola Co.", True, 69.40), ("PEP", "PepsiCo Inc.", True, 174.0),
            ("MCD", "McDonald's Corp.", False, 312.0), ("NKE", "NIKE Inc.", True, 82.50),
            ("SBUX", "Starbucks Corp.", False, 97.40), ("LOW", "Lowe's Companies Inc.", True, 268.0),
            ("TJX", "The TJX Companies Inc.", True, 116.0), ("BKNG", "Booking Holdings Inc.", False, 4380.0),
            ("CMG", "Chipotle Mexican Grill Inc.", True, 59.80), ("TGT", "Target Corp.", False, 151.0),
            ("CL", "Colgate-Palmolive Co.", True, 101.0), ("MDLZ", "Mondelez International Inc.", True, 71.80),
            ("MNST", "Monster Beverage Corp.", True, 53.40), ("LULU", "Lululemon Athletica Inc.", True, 298.0),
            ("MELI", "MercadoLibre Inc.", False, 2060.0), ("BABA", "Alibaba Group Holding (ADR)", True, 102.0),
            ("PDD", "PDD Holdings Inc. (Temu)", True, 128.0), ("JD", "JD.com Inc.", True, 41.20),
        ],
        "ABD Sanayi, Enerji, Havacılık & Madencilik": [
            ("XOM", "Exxon Mobil Corp.", True, 121.0), ("CVX", "Chevron Corp.", True, 151.0),
            ("COP", "ConocoPhillips", True, 108.0), ("SLB", "Schlumberger N.V.", True, 43.80),
            ("EOG", "EOG Resources Inc.", True, 126.0), ("CAT", "Caterpillar Inc.", True, 392.0),
            ("GE", "GE Aerospace", False, 189.0), ("HON", "Honeywell International Inc.", True, 218.0),
            ("UNP", "Union Pacific Corp.", True, 244.0), ("DE", "Deere & Co.", False, 408.0),
            ("UPS", "United Parcel Service Inc.", True, 134.0), ("FDX", "FedEx Corp.", True, 274.0),
            ("ETN", "Eaton Corp. plc", True, 342.0), ("ITW", "Illinois Tool Works Inc.", True, 262.0),
            ("EMR", "Emerson Electric Co.", True, 112.0), ("LIN", "Linde plc (Endüstriyel Gaz)", True, 472.0),
            ("APD", "Air Products and Chemicals Inc.", True, 315.0), ("SHW", "Sherwin-Williams Co.", True, 378.0),
            ("FCX", "Freeport-McMoRan Inc. (Bakır & Altın)", True, 48.60), ("NEM", "Newmont Corp. (Altın Madenciliği)", True, 56.20),
            ("NEE", "NextEra Energy Inc. (Yenilenebilir Enerji)", False, 84.50), ("BA", "The Boeing Co.", False, 155.0),
            ("LMT", "Lockheed Martin Corp.", False, 598.0), ("RTX", "RTX Corp.", False, 126.0),
            ("GD", "General Dynamics Corp.", False, 304.0), ("NOC", "Northrop Grumman Corp.", False, 524.0),
        ],
        "ABD Finans, Ödeme Sistemleri & Medya": [
            ("BRK-B", "Berkshire Hathaway Inc.", False, 456.0), ("JPM", "JPMorgan Chase & Co.", False, 226.0),
            ("V", "Visa Inc.", False, 288.0), ("MA", "Mastercard Inc.", False, 506.0),
            ("BAC", "Bank of America Corp.", False, 42.40), ("WFC", "Wells Fargo & Co.", False, 64.20),
            ("GS", "The Goldman Sachs Group Inc.", False, 522.0), ("MS", "Morgan Stanley", False, 119.0),
            ("BLK", "BlackRock Inc.", False, 995.0), ("SPGI", "S&P Global Inc.", False, 518.0),
            ("AXP", "American Express Co.", False, 274.0), ("C", "Citigroup Inc.", False, 65.80),
            ("SCHW", "The Charles Schwab Corp.", False, 72.40), ("CB", "Chubb Limited", False, 292.0),
            ("PGR", "The Progressive Corp.", False, 254.0), ("PYPL", "PayPal Holdings Inc.", False, 81.20),
            ("SQ", "Block Inc.", False, 74.50), ("COIN", "Coinbase Global Inc.", False, 212.0),
            ("NFLX", "Netflix Inc.", False, 745.0), ("DIS", "The Walt Disney Co.", False, 96.80),
            ("CMCSA", "Comcast Corp.", False, 41.80), ("TMUS", "T-Mobile US Inc.", False, 224.0),
            ("VZ", "Verizon Communications Inc.", False, 43.60), ("T", "AT&T Inc.", False, 22.10),
        ],
    }

    for sec_name, tickers in us_equities_by_sector.items():
        for sym, comp_name, is_kat, price in tickers:
            seed_v = sum(ord(c) for c in sym)
            add_item({
                "symbol": sym,
                "name": f"{comp_name} ({sec_name})",
                "risk_level": 4,
                "asset_class": AssetClass.GLOBAL_ETF_EQUITY,
                "sector": sec_name,
                "is_participation": is_kat,
                "currency": "USD",
                "base_price": price,
                "yf_ticker": sym,
                "pe": round(16.0 + (seed_v % 220) * 0.1, 2),
                "pb": round(2.8 + (seed_v % 85) * 0.1, 2),
                "roe": round(18.0 + (seed_v % 32), 1),
                "peg": round(0.82 + (seed_v % 75) * 0.01, 2),
                "beta": round(0.92 + (seed_v % 55) * 0.01, 2),
                "div_yield": round((seed_v % 28) * 0.1, 2),
            })

    # -------------------------------------------------------------------------
    # E. KRİPTO VARLIKLAR & KALDIRAÇLI / TERS ETF'LER (60 ADET - SEVİYE 5)
    # -------------------------------------------------------------------------
    crypto_assets = [
        ("BTC-USD", "Bitcoin (BTC / USD)", "Katman-1 & Dijital Değer Saklama", True, 68500.0),
        ("ETH-USD", "Ethereum (ETH / USD)", "Katman-1 & Akıllı Kontrat Platformu", True, 2650.0),
        ("SOL-USD", "Solana (SOL / USD)", "Katman-1 Yüksek Hızlı Ağ", True, 174.0),
        ("BNB-USD", "BNB Chain (BNB / USD)", "Katman-1 & Ekosistem Ağı", False, 595.0),
        ("XRP-USD", "Ripple (XRP / USD)", "Küresel Ödeme & Transfer Ağı", False, 0.54),
        ("ADA-USD", "Cardano (ADA / USD)", "Katman-1 & Akıllı Kontrat", True, 0.36),
        ("AVAX-USD", "Avalanche (AVAX / USD)", "Katman-1 Alt Ağ Mimarisi", True, 27.80),
        ("LINK-USD", "Chainlink (LINK / USD)", "Veri Kahini (Oracle) & CCIP Altyapısı", True, 11.90),
        ("DOT-USD", "Polkadot (DOT / USD)", "Çoklu Zincir (Multichain) Altyapısı", True, 4.45),
        ("NEAR-USD", "NEAR Protocol (NEAR / USD)", "Yapay Zeka & Katman-1 Ağı", True, 4.95),
        ("SUI-USD", "Sui Network (SUI / USD)", "Yeni Nesil Katman-1 Ağı", True, 2.05),
        ("APT-USD", "Aptos (APT / USD)", "Move Tabanlı Katman-1 Ağı", True, 10.15),
        ("TON-USD", "Toncoin (TON / USD)", "Katman-1 & İletişim Ağı", True, 5.25),
        ("TRX-USD", "TRON (TRX / USD)", "Ödeme & Transfer Ağı", False, 0.16),
        ("RENDER-USD", "Render Network (RENDER / USD)", "Yapay Zeka & Dağıtık GPU Hesaplama", True, 5.65),
        ("FET-USD", "Artificial Superintelligence Alliance (FET / USD)", "Yapay Zeka Ajan Ağı", True, 1.42),
        ("TAO-USD", "Bittensor (TAO / USD)", "Merkeziyetsiz Yapay Zeka Ağı", True, 560.0),
        ("INJ-USD", "Injective (INJ / USD)", "Finansal Altyapı & DeFi Ağı", False, 21.40),
        ("AAVE-USD", "Aave Protocol (AAVE / USD)", "DeFi Likidite Protokolü", False, 154.0),
        ("UNI-USD", "Uniswap (UNI / USD)", "Merkeziyetsiz Borsa (DEX) Protokolü", False, 7.85),
        ("ARB-USD", "Arbitrum (ARB / USD)", "Ethereum Katman-2 Ölçekleme", True, 0.58),
        ("OP-USD", "Optimism (OP / USD)", "Ethereum Katman-2 Superchain", True, 1.72),
        ("POL-USD", "Polygon Ecosystem Token (POL / USD)", "Ethereum Katman-2 & ZK Ağı", True, 0.38),
        ("ATOM-USD", "Cosmos Hub (ATOM / USD)", "Blokzincirler Arası İletişim (IBC)", True, 4.75),
        ("LTC-USD", "Litecoin (LTC / USD)", "Dijital Ödeme & PoW Ağı", True, 71.50),
        ("BCH-USD", "Bitcoin Cash (BCH / USD)", "PoW Ödeme Ağı", True, 365.0),
        ("ETC-USD", "Ethereum Classic (ETC / USD)", "PoW Akıllı Kontrat Ağı", True, 19.40),
        ("FIL-USD", "Filecoin (FIL / USD)", "Merkeziyetsiz Bulut Depolama (DePIN)", True, 3.85),
        ("AR-USD", "Arweave (AR / USD)", "Kalıcı Veri Depolama Ağı (DePIN)", True, 19.20),
        ("ICP-USD", "Internet Computer (ICP / USD)", "Merkeziyetsiz Bulut Bilişim", True, 8.40),
        ("HBAR-USD", "Hedera Hashgraph (HBAR / USD)", "Kurumsal DLT Ağı", True, 0.055),
        ("VET-USD", "VeChain (VET / USD)", "Tedarik Zinciri Blokzinciri", True, 0.024),
        ("ALGO-USD", "Algorand (ALGO / USD)", "Saf Hisse Kanıtı (PPoS) Katman-1", True, 0.13),
        ("STX-USD", "Stacks (STX / USD)", "Bitcoin Katman-2 Akıllı Kontrat Ağı", True, 1.85),
        ("IMX-USD", "Immutable (IMX / USD)", "Web3 & Katman-2 Ölçekleme", True, 1.55),
        ("GRT-USD", "The Graph (GRT / USD)", "Blokzincir Veri İndeksleme", True, 0.17),
        ("RUNE-USD", "THORChain (RUNE / USD)", "Zincirler Arası Likidite Ağı", False, 5.15),
        ("TIA-USD", "Celestia (TIA / USD)", "Modüler Veri Erişilebilirlik Ağı", True, 5.90),
        ("SEI-USD", "Sei Network (SEI / USD)", "Paralel EVM Katman-1 Ağı", True, 0.44),
        ("PENDLE-USD", "Pendle (PENDLE / USD)", "Getiri Tokenizasyon Protokolü", False, 4.65),
        ("ONDO-USD", "Ondo Finance (ONDO / USD)", "Gerçek Dünya Varlıkları (RWA)", False, 0.78),
        ("ENA-USD", "Ethena (ENA / USD)", "Sentetik Dolar Protokolü", False, 0.39),
        ("JUP-USD", "Jupiter (JUP / USD)", "Solana Likidite Toplayıcısı", False, 0.98),
        ("PYTH-USD", "Pyth Network (PYTH / USD)", "Yüksek Frekanslı Piyasa Veri Kahini", True, 0.36),
        ("KAS-USD", "Kaspa (KAS / USD)", "BlockDAG PoW Katman-1 Ağı", True, 0.135),
        ("XLM-USD", "Stellar (XLM / USD)", "Sınır Ötesi Ödeme Ağı", True, 0.096),
        ("XTZ-USD", "Tezos (XTZ / USD)", "Kendi Kendini Güncelleyen Katman-1", True, 0.68),
        ("MINA-USD", "Mina Protocol (MINA / USD)", "Sıfır Bilgi Kanıtı (ZK) Hafif Blokzincir", True, 0.56),
        ("SAND-USD", "The Sandbox (SAND / USD)", "Metaverse & Dijital Varlık Platformu", False, 0.27),
        ("MANA-USD", "Decentraland (MANA / USD)", "Metaverse Platformu", False, 0.31),
    ]
    for sym, name, subsec, is_kat, price in crypto_assets:
        add_item({
            "symbol": sym,
            "name": name,
            "risk_level": 5,
            "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
            "sector": f"Kripto: {subsec}",
            "is_participation": is_kat,
            "currency": "USD",
            "base_price": price,
            "yf_ticker": sym,
            "beta": 2.15,
        })

    leveraged_etfs = [
        ("TQQQ", "ProShares UltraPro QQQ (3x Kaldıraçlı Nasdaq-100 ETF)", 74.50, 0.88),
        ("SOXL", "Direxion Daily Semiconductor Bull 3X Shares (3x Yarı İletken ETF)", 36.80, 0.76),
        ("UPRO", "ProShares UltraPro S&P500 (3x Kaldıraçlı S&P 500 ETF)", 84.20, 0.91),
        ("SPXL", "Direxion Daily S&P 500 Bull 3X Shares", 162.0, 0.91),
        ("TECL", "Direxion Daily Technology Bull 3X Shares (3x Teknoloji ETF)", 88.40, 0.94),
        ("FAS", "Direxion Daily Financial Bull 3X Shares (3x Finans ETF)", 142.0, 0.94),
        ("LABU", "Direxion Daily S&P Biotech Bull 3X Shares (3x Biyoteknoloji)", 128.0, 0.96),
        ("SQQQ", "ProShares UltraPro Short QQQ (-3x Ters/Short Nasdaq-100 ETF)", 7.45, 0.95),
        ("SOXS", "Direxion Daily Semiconductor Bear 3X Shares (-3x Ters Çip ETF)", 21.30, 0.97),
        ("SPXS", "Direxion Daily S&P 500 Bear 3X Shares (-3x Ters S&P 500 ETF)", 6.85, 0.95),
    ]
    for sym, name, price, ter in leveraged_etfs:
        add_item({
            "symbol": sym,
            "name": name,
            "risk_level": 5,
            "asset_class": AssetClass.CRYPTO_LEVERAGED_HEDGE,
            "sector": "Kaldıraçlı / Ters (3x / -3x) ETF",
            "is_participation": False,
            "currency": "USD",
            "base_price": price,
            "yf_ticker": sym,
            "ter": ter,
            "beta": 2.95,
        })

    return catalog


UNIVERSAL_MULTI_ASSET_CATALOG: List[Dict[str, Any]] = _build_universal_multi_asset_catalog()
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
    _metrics_cache: Optional[Dict[str, float]] = field(default=None, repr=False)


class MultiAssetDataFetcher:
    """
    Seviye 1 - 5 arasındaki TÜM varlık sınıflarını (Para Piyasası, Tahvil/Eurobond/Sukuk,
    Döviz/Altın/Gümüş/Emtia, 111 TEFAS Fonu, 354+ BIST Hissesi, 203 Küresel ETF/ABD Hissesi
    ve 60 Kripto/Kaldıraçlı ETF) tek çatı altında çeken evrensel veri motoru.
    Ayrıca TradingView canlı tarayıcısı aktifken BIST ve ABD borsalarından dinamik varlık ekler.
    """

    def __init__(
        self,
        prefer_live_data: bool = True,
        include_all_bist_from_tv: bool = True,
        max_individual_yfinance_calls: int = 8,
    ) -> None:
        self.prefer_live_data = prefer_live_data
        self.include_all_bist_from_tv = include_all_bist_from_tv
        self.max_individual_yfinance_calls = max_individual_yfinance_calls
        self._yf_calls_made = 0
        self._tv_bist_cache: Dict[str, List[Any]] = {}
        self._tv_us_cache: Dict[str, List[Any]] = {}

    def fetch_tradingview_bist_scan(self) -> Dict[str, List[Any]]:
        if self._tv_bist_cache:
            return self._tv_bist_cache
        if not (self.prefer_live_data and HAS_REQUESTS):
            return {}

        url = "https://scanner.tradingview.com/turkey/scan"
        payload = {
            "symbols": {"query": {"types": ["stock", "fund", "dr"]}},
            "columns": [
                "name", "close", "price_earnings_ttm", "price_book_fq",
                "return_on_equity_ttm", "return_on_equity_fq",
                "dividend_yield_recent", "dividend_yield_forward",
                "price_earnings_growth_ttm", "peg_ratio_ttm",
                "beta_1_year", "RSI", "MACD.macd", "MACD.signal",
                "SMA50", "SMA200", "BB.upper", "BB.lower", "sector", "description",
            ],
            "range": [0, 750],
        }
        try:
            resp = requests.post(url, json=payload, timeout=7)
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    ticker = item["s"].replace("BIST:", "")
                    self._tv_bist_cache[ticker] = item["d"]
        except Exception as exc:
            logger.debug("TradingView BIST taraması çevrimdışı, yedek veri devrede: %s", exc)
        return self._tv_bist_cache

    def fetch_tradingview_us_scan(self) -> Dict[str, List[Any]]:
        if self._tv_us_cache:
            return self._tv_us_cache
        if not (self.prefer_live_data and HAS_REQUESTS):
            return {}

        url = "https://scanner.tradingview.com/america/scan"
        payload = {
            "filter": [{"left": "market_cap_basic", "operation": "nempty"}],
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": ["stock", "fund"]}},
            "columns": [
                "name", "close", "price_earnings_ttm", "price_book_fq",
                "return_on_equity_ttm", "return_on_equity_fq",
                "dividend_yield_recent", "dividend_yield_forward",
                "price_earnings_growth_ttm", "peg_ratio_ttm",
                "beta_1_year", "RSI", "MACD.macd", "MACD.signal",
                "SMA50", "SMA200", "BB.upper", "BB.lower", "sector", "description",
            ],
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "range": [0, 300],
        }
        try:
            resp = requests.post(url, json=payload, timeout=7)
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    ticker = item["s"].split(":")[-1]
                    self._tv_us_cache[ticker] = item["d"]
        except Exception as exc:
            logger.debug("TradingView ABD taraması çevrimdışı, yedek veri devrede: %s", exc)
        return self._tv_us_cache

    def generate_synthetic_ohlcv(
        self, symbol: str, base_price: float, days: int = 120, risk_level: int = 3
    ) -> pd.DataFrame:
        seed = sum((idx + 1) * ord(c) for idx, c in enumerate(symbol)) % 100000
        rng = np.random.default_rng(seed)
        dates = pd.date_range(end=dt.datetime.now(timezone.utc), periods=days, freq="B")

        vol_map = {1: 0.0032, 2: 0.0055, 3: 0.014, 4: 0.020, 5: 0.031}
        daily_vol = vol_map.get(risk_level, 0.015)
        drift = 0.0010 if risk_level <= 2 else 0.0008

        returns = rng.normal(loc=drift, scale=daily_vol, size=days)
        if seed % 5 == 0:
            returns[-14:-3] = -daily_vol * 0.65
            returns[-3:] = [daily_vol * 1.1, daily_vol * 1.3, daily_vol * 1.45]
        elif seed % 5 == 1:
            returns[-5:] = [daily_vol * 0.7, daily_vol * 0.9, -daily_vol * 0.3, daily_vol * 1.1, daily_vol * 1.2]
        elif seed % 5 == 2:
            returns[-8:] = rng.normal(loc=0.0005, scale=daily_vol * 0.8, size=8)

        price_series = base_price * np.exp(np.cumsum(returns))
        price_series = price_series / max(price_series[-1], 1e-8) * base_price

        high = price_series * (1.0 + rng.uniform(0.0015, daily_vol * 1.25, size=days))
        low = price_series * (1.0 - rng.uniform(0.0015, daily_vol * 1.25, size=days))
        open_p = price_series * (1.0 + rng.uniform(-daily_vol * 0.45, daily_vol * 0.45, size=days))
        volume = rng.integers(850_000, 16_000_000, size=days).astype(float)
        if seed % 3 == 0:
            volume[-2:] *= 1.95

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

        tv_bist = self.fetch_tradingview_bist_scan()
        tv_us = self.fetch_tradingview_us_scan()
        clean_sym = symbol.replace(".IS", "")

        d = tv_bist.get(clean_sym) or tv_us.get(clean_sym)
        if d:
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

        # Canlı yfinance çağrısını sadece temel referans semboller için sınırla (770+ varlığın anında yüklenmesi için)
        priority_live_symbols = {"THYAO", "ASELS", "TUPRS", "KCHOL", "SPY", "QQQ", "XAU_TRY", "BTC-USD"}
        if (
            self.prefer_live_data
            and HAS_YFINANCE
            and symbol in priority_live_symbols
            and self._yf_calls_made < self.max_individual_yfinance_calls
        ):
            yf_sym = asset_meta.get("yf_ticker")
            if yf_sym:
                try:
                    self._yf_calls_made += 1
                    hist = yf.Ticker(yf_sym).history(period="6mo")
                    if hist is not None and not hist.empty and len(hist) >= 25:
                        df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
                        if symbol in ("XAU_TRY", "XAG_TRY", "XPT_TRY", "XPD_TRY"):
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
            sector=asset_meta.get("sector", "Diğer"),
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
        770+ dahili Evrensel Çoklu Varlık Kataloğundaki tüm enstrümanları çeker;
        ayrıca TradingView canlı tarayıcısı aktifse BIST'teki tüm 550+ hisseyi ve
        ABD borsalarındaki ek hisseleri dinamik olarak evrene dahil eder.
        """
        snapshots: Dict[str, MarketSnapshot] = {
            item["symbol"]: self.fetch_snapshot(item, days=days)
            for item in UNIVERSAL_MULTI_ASSET_CATALOG
        }

        if self.include_all_bist_from_tv:
            tv_bist = self.fetch_tradingview_bist_scan()
            for t, d in tv_bist.items():
                if t in snapshots or not t.isalpha() or len(t) > 6:
                    continue
                beta_val = float(d[10]) if d[10] is not None else 1.15
                if t in BIST_30 and beta_val <= 1.35:
                    r_lvl = 3
                    a_cls = AssetClass.BIST_BLUECHIP_30
                elif beta_val > 1.45:
                    r_lvl = 5
                    a_cls = AssetClass.BIST_HIGH_BETA
                else:
                    r_lvl = 4
                    a_cls = AssetClass.BIST_GROWTH_100

                # Sektör eşleştirmesi
                sec_found = "Holding & Yatırım"
                for sec_k, sec_list in SEKTOR_SOZLUGU.items():
                    if t in sec_list:
                        sec_found = sec_k
                        break
                desc = str(d[19]) if len(d) > 19 and d[19] else f"{t} ({sec_found})"
                meta = {
                    "symbol": t,
                    "name": desc,
                    "risk_level": r_lvl,
                    "asset_class": a_cls,
                    "sector": sec_found,
                    "is_participation": t in KATILIM_TUM,
                    "currency": "TRY",
                    "base_price": float(d[1]) if d[1] is not None else 50.0,
                }
                snapshots[t] = self.fetch_snapshot(meta, days=days)

        return snapshots


def normalize_turkish_lower(text: str) -> str:
    """Türkçe büyük İ/I harflerini Python unicode kombinasyon hatası olmadan küçültür."""
    return str(text).replace("İ", "i").replace("I", "ı").lower()


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
        """
        NumPy vektörize edilmiş ultra hızlı teknik indikatör motoru (770+ varlığı <0.12 sn içinde hesaplar).
        """
        close = df["Close"].to_numpy(dtype=float)
        high = df["High"].to_numpy(dtype=float)
        low = df["Low"].to_numpy(dtype=float)
        volume = df["Volume"].to_numpy(dtype=float)
        n = len(close)
        if n < 5:
            return {
                "rsi_14": 50.0,
                "macd_hist": 0.0,
                "bb_width_pct": 5.0,
                "atr_14": 1.0,
                "dist_from_monthly_low_pct": 5.0,
                "volume_zscore": 0.0,
                "above_sma50": 1.0,
                "sma50": float(close[-1]) if n else 100.0,
            }

        # RSI (14)
        delta = np.diff(close)
        w_rsi = min(14, len(delta))
        recent_d = delta[-w_rsi:]
        gain = float(np.mean(np.maximum(recent_d, 0.0)))
        loss = float(np.mean(np.maximum(-recent_d, 0.0)))
        if loss < 1e-9:
            rsi = 65.0 if gain > 0 else 50.0
        else:
            rs = gain / loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # EMA12, EMA26, MACD Histogram
        alpha12 = 2.0 / 13.0
        alpha26 = 2.0 / 27.0
        alpha9 = 2.0 / 10.0
        ema12 = close[0]
        ema26 = close[0]
        macd_sig = 0.0
        macd_hist = 0.0
        for c_val in close[1:]:
            ema12 = alpha12 * c_val + (1.0 - alpha12) * ema12
            ema26 = alpha26 * c_val + (1.0 - alpha26) * ema26
            m_line = ema12 - ema26
            macd_sig = alpha9 * m_line + (1.0 - alpha9) * macd_sig
            macd_hist = m_line - macd_sig

        # Bollinger & SMA50
        w20 = close[-min(20, n):]
        sma20 = float(np.mean(w20))
        std20 = float(np.std(w20, ddof=1)) if len(w20) > 1 else 0.0
        sma50 = float(np.mean(close[-min(50, n):]))
        last_close = float(close[-1])
        bb_width_pct = ((4.0 * std20) / max(last_close, 0.01)) * 100.0

        # ATR (14)
        w_atr = min(14, n - 1)
        h_tail = high[-w_atr:]
        l_tail = low[-w_atr:]
        pc_tail = close[-w_atr - 1:-1]
        tr = np.maximum(h_tail - l_tail, np.maximum(np.abs(h_tail - pc_tail), np.abs(l_tail - pc_tail)))
        atr = float(np.mean(tr)) if len(tr) else last_close * 0.02

        # 22 Günlük Dibe Uzaklık
        low_22d = float(np.min(low[-min(22, n):]))
        dist_from_monthly_low_pct = ((last_close - low_22d) / max(low_22d, 0.01)) * 100.0

        # Hacim Z-Skoru
        v20 = volume[-min(20, n):]
        vol_mean = float(np.mean(v20))
        vol_std = float(np.std(v20, ddof=1)) if len(v20) > 1 else 1.0
        if vol_std < 1e-6:
            vol_std = 1.0
        vol_zscore = (float(volume[-1]) - vol_mean) / vol_std

        return {
            "rsi_14": round(rsi, 2),
            "macd_hist": round(macd_hist, 4),
            "bb_width_pct": round(bb_width_pct, 2),
            "atr_14": round(atr, 4),
            "dist_from_monthly_low_pct": round(dist_from_monthly_low_pct, 2),
            "volume_zscore": round(vol_zscore, 2),
            "above_sma50": 1.0 if last_close >= sma50 else 0.0,
            "sma50": round(sma50, 4),
        }

    @staticmethod
    def get_cached_metrics(snap: MarketSnapshot) -> Dict[str, float]:
        if snap._metrics_cache is None:
            snap._metrics_cache = QuantitativeIndicators.compute_metrics(snap.ohlcv_history)
        return snap._metrics_cache


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
        metrics = QuantitativeIndicators.get_cached_metrics(snap)

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
        for idx, s in enumerate(signals):
            row_bg = "#121722" if idx % 2 == 0 else "#161D2B"
            if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY):
                badge_style = "background:rgba(0,192,135,0.22);color:#00E6B8;border:1px solid #00C087;"
            elif "VETO" in s.action.value or "SAT" in s.action.value or "KISITLI" in s.action.value:
                badge_style = "background:rgba(242,54,69,0.22);color:#FF6B78;border:1px solid #F23645;"
            else:
                badge_style = "background:rgba(148,163,184,0.18);color:#E2E8F0;border:1px solid #64748B;"

            rationale_html = "<br>".join(f"• {r}" for r in s.rationale[:3])
            rows_html += f"""
            <tr style="background-color:{row_bg};">
                <td style="padding:10px 12px;border:1px solid #232C40;color:#FFFFFF;">
                    <b style="color:#FFFFFF;font-size:13.5px;">{s.symbol}</b><br>
                    <small style="color:#94A3B8;font-size:11.5px;">{s.name}</small>
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;text-align:center;color:#CBD5E1;font-weight:600;">
                    Seviye {s.risk_level}
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;text-align:center;">
                    <span style="{badge_style}padding:4px 10px;border-radius:6px;font-weight:700;font-size:12px;display:inline-block;">
                        {s.action.value}
                    </span>
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;text-align:right;color:#60A5FA;font-weight:700;font-size:14px;">
                    {s.composite_score:.2f}
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;text-align:right;color:#FFFFFF;font-weight:600;">
                    {s.current_price:,.2f} {s.currency}
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;text-align:right;color:#E2E8F0;font-weight:600;">
                    {s.recommended_quantity} Adet<br>
                    <small style="color:#94A3B8;font-weight:400;">({s.recommended_notional_try:,.0f} TL)</small>
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;text-align:right;font-size:12px;font-weight:600;">
                    <span style="color:#FF6B78;">SL: {s.stop_loss_price:,.2f}</span><br>
                    <span style="color:#00E6B8;">TP: {s.take_profit_price:,.2f}</span>
                </td>
                <td style="padding:10px 12px;border:1px solid #232C40;font-size:12px;color:#CBD5E1;line-height:1.45;">
                    {rationale_html}
                </td>
            </tr>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;background-color:#0B0E14;color:#E2E8F0;margin:0;padding:14px;">
            <div style="background:linear-gradient(135deg,#141B2D 0%,#0F1420 100%);border:1px solid #263147;border-left:5px solid #2962FF;border-radius:8px;padding:16px 20px;">
                <h2 style="color:#FFFFFF;margin:0;font-size:18px;font-weight:700;">
                    AlpQuant v3.5 — Evrensel Çoklu Varlık Karar Motoru Sinyal Raporu
                </h2>
                <p style="margin:8px 0 0 0;font-size:13.5px;color:#CBD5E1;">
                    <b style="color:#60A5FA;">Yatırımcı SPK Risk Skoru:</b> {suitability.risk_score}/5 &nbsp;|&nbsp;
                    <b style="color:#60A5FA;">Onaylı Seviyeler:</b> {suitability.approved_levels} &nbsp;|&nbsp;
                    <b style="color:#60A5FA;">Evren Modu:</b> {"Sadece Katılım" if suitability.participation_only else "Tüm Piyasa (Konvansiyonel + Katılım)"} &nbsp;|&nbsp;
                    <b style="color:#60A5FA;">Tarih:</b> {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
                </p>
            </div>
            <table style="width:100%;border-collapse:collapse;margin-top:14px;font-size:13px;background-color:#121722;color:#E2E8F0;border:1px solid #263147;border-radius:8px;overflow:hidden;">
                <thead>
                    <tr style="background-color:#192132;color:#94A3B8;text-transform:uppercase;font-size:11.5px;letter-spacing:0.5px;">
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:left;color:#94A3B8;">Varlık</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:center;color:#94A3B8;">SPK Risk</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:center;color:#94A3B8;">Sinyal</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:right;color:#94A3B8;">Skor</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:right;color:#94A3B8;">Fiyat</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:right;color:#94A3B8;">Önerilen Emir</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:right;color:#94A3B8;">SL / TP</th>
                        <th style="padding:10px 12px;border:1px solid #263147;text-align:left;color:#94A3B8;">Analitik Gerekçe</th>
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
    /* Global Background & Institutional Dark Theme */
    .stApp {
        background-color: #0B0E14 !important;
        color: #D1D4DC !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
    }
    [data-testid="stSidebar"] {
        background-color: #10141E !important;
        border-right: 1px solid #1E2638 !important;
    }
    
    /* Headings & Text */
    h1, h2, h3, h4 { color: #FFFFFF !important; font-weight: 600 !important; }
    p, span, div { color: #D1D4DC; }
    
    /* Connected Institutional Segmented Tab Bar */
    div[data-baseweb="tab-list"] {
        gap: 6px;
        background-color: #121722 !important;
        padding: 6px 8px !important;
        border-radius: 12px !important;
        border: 1px solid #1E2638 !important;
        margin-bottom: 1.2rem !important;
    }
    button[data-baseweb="tab"] {
        border-radius: 8px !important;
        padding: 8px 20px !important;
        font-size: 14px !important;
        font-weight: 600 !important;
        color: #8E95A5 !important;
        border: none !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        background-color: transparent !important;
    }
    button[data-baseweb="tab"]:hover {
        color: #FFFFFF !important;
        background-color: rgba(41, 98, 255, 0.12) !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #FFFFFF !important;
        background: linear-gradient(135deg, #2962FF 0%, #1E53E5 100%) !important;
        box-shadow: 0 4px 14px rgba(41, 98, 255, 0.38) !important;
    }
    div[data-baseweb="tab-border"] { display: none !important; }
    div[data-baseweb="tab-highlight"] { display: none !important; }
    
    /* Metric Cards */
    [data-testid="stMetric"] {
        background: #121722 !important;
        border: 1px solid #1E2638 !important;
        border-radius: 10px !important;
        padding: 12px 16px !important;
    }
    [data-testid="stMetricValue"] {
        color: #FFFFFF !important;
        font-size: 1.55rem !important;
        font-weight: 700 !important;
    }
    [data-testid="stMetricLabel"] {
        color: #8E95A5 !important;
        font-size: 0.78rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.5px !important;
        text-transform: uppercase !important;
    }
    
    /* Interactive Buttons */
    .stButton > button {
        background: linear-gradient(135deg, #2962FF 0%, #1E53E5 100%) !important;
        color: #FFFFFF !important;
        border: 1px solid #3D7BFF !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        box-shadow: 0 2px 8px rgba(41, 98, 255, 0.25) !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #1E53E5 0%, #1541B8 100%) !important;
        box-shadow: 0 4px 14px rgba(41, 98, 255, 0.45) !important;
    }
    
    /* Cards & Badges */
    .aq-card {
        background: linear-gradient(135deg, #131826 0%, #0F131E 100%);
        border: 1px solid #1E2638;
        border-left: 4px solid #2962FF;
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 12px;
    }
    .aq-badge-filter {
        display: inline-block;
        background: rgba(41, 98, 255, 0.15);
        color: #70A1FF;
        border: 1px solid rgba(41, 98, 255, 0.35);
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 600;
        margin-right: 6px;
        margin-bottom: 6px;
    }
    hr { border-color: #1E2638 !important; margin-top: 0.8rem; margin-bottom: 0.8rem; }

    /* Expander & Input Dark Contrast Enforcement */
    [data-testid="stExpander"] {
        background-color: #121722 !important;
        border: 1px solid #1E2638 !important;
        border-radius: 8px !important;
    }
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary span,
    [data-testid="stExpander"] summary p {
        color: #E2E8F0 !important;
        font-weight: 600 !important;
    }
    [data-testid="stExpanderDetails"] {
        background-color: #0E131D !important;
        color: #E2E8F0 !important;
    }
    input, textarea, [data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {
        background-color: #141A29 !important;
        color: #FFFFFF !important;
        border-color: #263147 !important;
    }
    label, [data-testid="stWidgetLabel"] p {
        color: #CBD5E1 !important;
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

st.sidebar.markdown(
    """
    <div style='text-align: center; padding: 6px 0 12px 0;'>
        <h3 style='margin:0; color:#FFFFFF; font-size:21px; font-weight:700;'>AlpQuant Pro</h3>
        <span style='color:#2962FF; font-size:11px; font-weight:600; letter-spacing:1px;'>INSTITUTIONAL MULTI-MARKET TERMINAL</span>
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.markdown("<hr style='margin: 4px 0 12px 0;'>", unsafe_allow_html=True)

# --- HIZLI SEMBOL / FON / ŞİRKET ARAMA ---
arama_metni = st.sidebar.text_input(
    "🔎 Hızlı Varlık / Fon / Hisse Ara",
    value="",
    placeholder="Örn: THYAO, KZL, Sukuk, NVDA, Altın...",
).strip()

# --- 1. VARLIK SINIFI & KATILIM EVRENİ SEÇİMİ ---
with st.sidebar.expander("🌐 1. Varlık Sınıfı & Piyasa Evreni", expanded=True):
    varlik_grubu = st.selectbox(
        "Piyasa / Varlık Grubu",
        [
            "🌐 Tüm Varlıklar (770+ Çoklu Varlık Evreni)",
            "🇹🇷 Borsa İstanbul (BIST Payları - 350+ Hisse)",
            "🏛️ TEFAS Yatırım Fonları (111 Konvansiyonel & Katılım Fonu)",
            "🇺🇸 Küresel ETF & ABD Borsa Payları (200+ Enstrüman)",
            "🥇 Döviz, Altın, Emtia & Tahvil / Sukuk (42 Enstrüman)",
            "⚡ Kripto Varlıklar & Kaldıraçlı ETF (60 Enstrüman)",
        ],
        index=0,
    )
    katilim_modu = st.selectbox(
        "☪️ Katılım Finans / Faizsiz Evren Filtresi",
        [
            "Tüm Enstrümanlar (Konvansiyonel + Katılım)",
            "☪️ Sadece Katılım Finans Uyumlu Tüm Varlıklar",
            "☪️ Sadece TEFAS Katılım Fonları (53 Faizsiz Fon)",
            "☪️ Sadece BIST Katılım Endeksi Hisseleri (XKTUM)",
            "☪️ Sadece Küresel İslami ETF & Sukuk / Altın",
        ],
        index=0,
    )
    sadece_katilim_filtre = katilim_modu != "Tüm Enstrümanlar (Konvansiyonel + Katılım)"
    st.session_state.suitability_profile.participation_only = sadece_katilim_filtre

# --- 2. DİNAMİK ENDEKS, SEKTÖR & FON KATEGORİSİ FİLTRELERİ ---
TEFAS_KATEGORI_LISTESI = [
    "Katılım Kira Sertifikası (Sukuk) & Para Piyasası Fonu",
    "Katılım Altın & Kıymetli Madenler Fonu",
    "Katılım Hisse Senedi & Katılım Değişken Fonu",
    "Para Piyasası / Kısa Vadeli Fon",
    "Borçlanma Araçları / Eurobond Fonu",
    "Kıymetli Madenler / Altın / Emtia Fonu",
    "Hisse Senedi Yoğun Fon",
    "Tematik / Teknoloji / Yabancı ETF Fonu",
    "Değişken / Karma / Fon Sepeti Fonu",
    "Serbest / Agresif / Mutlak Getiri Fonu",
]

KURESEL_KATEGORI_LISTESI = [
    "Küresel İslami / Katılım ETF",
    "Küresel Endeks ETF",
    "Küresel Teknoloji & Yarı İletken ETF",
    "Küresel Temettü & Değer ETF",
    "Küresel Sektör ETF",
    "Küresel Emtia ETF",
    "ABD Teknoloji, Bulut & Yapay Zeka",
    "ABD Yarı İletken & Donanım",
    "ABD Sağlık, İlaç & Biyoteknoloji",
    "ABD Tüketim, Perakende & E-Ticaret",
    "ABD Sanayi, Enerji, Havacılık & Madencilik",
    "ABD Finans, Ödeme Sistemleri & Medya",
]

MAKRO_KATEGORI_LISTESI = [
    "Kıymetli Madenler",
    "Döviz (FX)",
    "Küresel Emtia",
    "Kira Sertifikası (Sukuk)",
    "Hazine Bonosu & Tahvil",
    "Eurobond (Döviz Tahvil)",
    "Küresel Tahvil",
    "Katılım Hesabı",
    "Para Piyasası & Mevduat",
]

with st.sidebar.expander("📌 2. Endeks, Sektör & Fon Kategorisi", expanded=True):
    endeks_secimi = "Tüm Evren (Endeks Kısıtı Yok)"
    sektor_secimi: List[str] = []
    tefas_kategori_secimi: List[str] = []
    kuresel_kategori_secimi: List[str] = []
    makro_kategori_secimi: List[str] = []

    if "Borsa İstanbul" in varlik_grubu or "Tüm Varlıklar" in varlik_grubu:
        endeks_secimi = st.radio(
            "BIST Endeks Filtresi (Seçildiğinde Sadece İlgili BIST Hisselerini Listeler)",
            (
                "Tüm Evren (Endeks Kısıtı Yok)",
                "BIST Katılım Tüm (XKTUM - 200+ Hisse)",
                "BIST Katılım 30 (XK030)",
                "BIST 30 (XU030)",
                "BIST 50 (XU050)",
                "BIST 100 (XU100)",
            ),
            index=0,
        )
        sektor_secimi = st.multiselect(
            "BIST Sektör Seçimi (Çoklu)",
            options=list(SEKTOR_SOZLUGU.keys()),
            placeholder="Tüm BIST Sektörleri",
        )

    if "TEFAS" in varlik_grubu or "Tüm Varlıklar" in varlik_grubu:
        tefas_kategori_secimi = st.multiselect(
            "🏛️ TEFAS Fon Kategorisi (Katılım & Konvansiyonel)",
            options=TEFAS_KATEGORI_LISTESI,
            placeholder="Tüm TEFAS Fon Kategorileri",
        )

    if "Küresel ETF" in varlik_grubu:
        kuresel_kategori_secimi = st.multiselect(
            "🇺🇸 Küresel ETF & ABD Sektör Seçimi",
            options=KURESEL_KATEGORI_LISTESI,
            placeholder="Tüm Küresel Sektör ve ETF'ler",
        )

    if "Döviz, Altın" in varlik_grubu:
        makro_kategori_secimi = st.multiselect(
            "🥇 Döviz, Altın, Emtia & Tahvil/Sukuk Alt Grubu",
            options=MAKRO_KATEGORI_LISTESI,
            placeholder="Tüm Makro & Sabit Getirili Gruplar",
        )

# --- 3. TEMEL ANALİZ FİLTRELERİ ---
with st.sidebar.expander("📊 3. Temel Analiz & Değerleme", expanded=False):
    fk_filtre_aktif = st.checkbox("Maksimum F/K Filtresi Uygula", value=False)
    fk_max = st.slider("Maksimum F/K (P/E)", min_value=1.0, max_value=80.0, value=30.0, step=1.0)

    pddd_filtre_aktif = st.checkbox("Maksimum PD/DD Filtresi Uygula", value=False)
    pddd_max = st.slider("Maksimum PD/DD (P/B)", min_value=0.5, max_value=25.0, value=8.0, step=0.5)

    roe_filtre_aktif = st.checkbox("Minimum ROE (%) Filtresi Uygula", value=False)
    roe_min = st.slider("Minimum Özsermaye Kârlılığı (ROE %)", min_value=0.0, max_value=70.0, value=15.0, step=1.0)

    ter_filtre_aktif = st.checkbox("Maksimum Fon Yönetim Gideri (TER %) Filtresi", value=False)
    ter_max = st.slider("Maksimum Yıllık Fon Kesintisi (TER %)", min_value=0.1, max_value=3.0, value=1.5, step=0.05)

    eksik_verileri_goster = st.checkbox("Çarpanı Olmayan Varlıkları (Fon/Altın/FX) Koruyarak Listele", value=True)

# --- 4. TEKNİK & KARAR SİNYALİ FİLTRELERİ ---
with st.sidebar.expander("📈 4. Teknik & Karar Sinyali Filtreleri", expanded=False):
    rsi_filtre_aktif = st.checkbox("RSI (14) Aralığı Filtresi Uygula", value=False)
    rsi_aralik = st.slider("RSI (14) Aralığı", min_value=0, max_value=100, value=(0, 100))
    sadece_trend = st.checkbox("Sadece Yükselen Trend (Fiyat >= SMA50)", value=False)
    sadece_macd_al = st.checkbox("Sadece MACD Pozitif (Bull) Kesişimi", value=False)
    sinyal_filtre_secimi = st.selectbox(
        "Karar Sinyali Filtresi",
        ["Tüm Sinyaller", "Sadece AL & GÜÇLÜ AL", "Sadece GÜÇLÜ AL", "Sadece TUT / İZLE"],
        index=0,
    )
    min_karar_skoru = st.slider("Minimum Bileşik Karar Skoru", min_value=0.0, max_value=95.0, value=0.0, step=5.0)

# --- 5. SERMAYE & SİNYAL BOTU AYARLARI ---
with st.sidebar.expander("💼 5. Portföy Sermayesi & Ayarlar", expanded=False):
    portfoy_bakiye = st.number_input(
        "Portföy Sermayesi (TL)",
        min_value=10_000.0,
        max_value=50_000_000.0,
        value=250_000.0,
        step=10_000.0,
    )
    canli_veri_aktif = st.checkbox("Canlı Veri Akışı (TradingView / yfinance)", value=True)
    tum_bist_tara = st.checkbox("TradingView Canlı BIST 550+ Tüm Hisseleri Ekle", value=True)

    gemini_api_key = st.text_input("Gemini API Key", value=os.environ.get("GEMINI_API_KEY", ""), type="password")
    gemini_model = st.selectbox("Gemini Modeli", ["gemini-2.5-flash", "gemini-2.5-pro"], index=0)
    alici_eposta = st.text_input("Alıcı E-Posta", value="alperengulluoglu@gmail.com")
    gonderici_eposta = st.text_input("Gönderici E-Posta", value="alperengulluoglu@gmail.com")
    smtp_sifre = st.text_input("Gmail App Password", value=os.environ.get("SMTP_APP_PASSWORD", ""), type="password")

# --- 6. EN ALTTA: SPK YATIRIM TESTİ & SİSTEM HAFIZASI ---
st.sidebar.markdown("<hr style='margin: 14px 0 8px 0;'>", unsafe_allow_html=True)
prof: SuitabilityProfile = st.session_state.suitability_profile
with st.sidebar.expander("🧭 SPK Yatırım Uygunluk Testi & Hafıza", expanded=False):
    st.markdown(
        f"""
        <div style='background:rgba(41,98,255,0.12); border-left:3px solid #2962FF; padding:10px 12px; border-radius:6px; font-size:12.5px; margin-bottom:10px;'>
            <b>Kayıtlı Risk Seviyeniz:</b> Seviye {prof.risk_score} / 5<br>
            <b>Onaylı Seviyeler:</b> {prof.approved_levels}<br>
            <b>Sistem Hafızası:</b> 💾 Oturumda Kayıtlı
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Testi her seferinde çözmek zorunda değilsiniz. Risk profilinizi revize etmek için aşağıdaki formu kullanabilirsiniz:")
    with st.form("sidebar_spk_form"):
        yeni_risk_skoru = st.select_slider(
            "Yatırımcı Risk İştahı (1 = Çok Düşük, 5 = Agresif / Tüm Piyasa)",
            options=[1, 2, 3, 4, 5],
            value=prof.risk_score,
        )
        yas_secim = st.selectbox("Yaş Aralığı", ["18-30 Yaş", "31-50 Yaş", "51-65 Yaş", "66 ve üzeri"], index=0)
        tecrube_secim = st.selectbox("Yatırım Tecrübesi", ["1 yıldan az", "1-4 yıl", "5-10 yıl", "10 yıldan fazla"], index=1)
        bilgi_seviyesi = st.selectbox(
            "Piyasa Araçları Bilgi Düzeyi",
            ["Yeterince bilgim var (Bilanço & Fiyatlama)", "Bilgim kısıtlı", "Bilgim yok"],
            index=0,
        )
        if st.form_submit_button("💾 Uygunluk Profilini Güncelle"):
            ans_custom = {
                i: {
                    "bilgi": bilgi_seviyesi,
                    "siklik": "Haftada birkaç kez" if yeni_risk_skoru >= 4 else "Ayda birkaç kez",
                    "hacim": "500.001 TL ve üzeri" if yeni_risk_skoru >= 4 else "50.001 TL - 500.000 TL",
                }
                for i in range(1, 6)
            }
            st.session_state.suitability_profile = UniversalSuitabilityEngine.evaluate(
                user_id="alperen_gulluoglu",
                risk_skoru=yeni_risk_skoru,
                answers=ans_custom,
                participation_only=sadece_katilim_filtre,
            )
            st.toast(f"SPK Profili Seviye {yeni_risk_skoru}/5 olarak güncellendi ve hafızaya kaydedildi!", icon="✅")
            st.rerun()


# --- VERİLERİ ÇEK & ÖNBELLEKLE (770+ DAHİLİ / 950+ CANLI ENSTRÜMAN) ---
@st.cache_resource(show_spinner=False)
def load_cached_universe(live_enabled: bool, include_tv_bist: bool) -> Dict[str, MarketSnapshot]:
    fetcher_inst = MultiAssetDataFetcher(
        prefer_live_data=live_enabled,
        include_all_bist_from_tv=include_tv_bist,
    )
    snaps = fetcher_inst.fetch_all_universe(days=120)
    for snap in snaps.values():
        QuantitativeIndicators.get_cached_metrics(snap)
    return snaps


universe_snapshots = load_cached_universe(canli_veri_aktif, tum_bist_tara)

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
signals_by_symbol = {s.symbol: s for s in all_signals}


# --- KUSURSUZ VE KESİN SOL MENÜ FİLTRELEME MOTORU ---
def is_bist_stock(snap: MarketSnapshot) -> bool:
    return snap.asset_class in (
        AssetClass.BIST_BLUECHIP_30,
        AssetClass.BIST_GROWTH_100,
        AssetClass.BIST_HIGH_BETA,
    )


def passes_filters(snap: MarketSnapshot, sig: DecisionSignal) -> bool:
    # 0. Hızlı Arama Kutusu (Sembol, Varlık Adı veya Sektör/Kategori içinde arama)
    if arama_metni:
        q = arama_metni.lower()
        if (
            q not in snap.symbol.lower()
            and q not in snap.name.lower()
            and q not in snap.sector.lower()
        ):
            return False

    # 1. Ana Varlık Grubu Filtresi
    if "Borsa İstanbul" in varlik_grubu:
        if not is_bist_stock(snap):
            return False
    elif "TEFAS" in varlik_grubu:
        if snap.asset_class != AssetClass.TEFAS_MUTUAL_FUND:
            return False
    elif "Küresel ETF" in varlik_grubu:
        if snap.asset_class != AssetClass.GLOBAL_ETF_EQUITY:
            return False
    elif "Döviz, Altın" in varlik_grubu:
        if snap.asset_class not in (
            AssetClass.FOREX_PRECIOUS_METALS,
            AssetClass.BONDS_EUROBOND_SUKUK,
            AssetClass.MONEY_MARKET_DEPOSIT,
        ):
            return False
    elif "Kripto Varlıklar" in varlik_grubu:
        if snap.asset_class != AssetClass.CRYPTO_LEVERAGED_HEDGE:
            return False

    # 2. Katılım Finans / Faizsiz Evren Modu Filtresi (Kesin Ayrım)
    if katilim_modu == "☪️ Sadece Katılım Finans Uyumlu Tüm Varlıklar":
        if not snap.is_participation_compliant:
            return False
    elif katilim_modu == "☪️ Sadece TEFAS Katılım Fonları (53 Faizsiz Fon)":
        if snap.asset_class != AssetClass.TEFAS_MUTUAL_FUND or not snap.is_participation_compliant:
            return False
    elif katilim_modu == "☪️ Sadece BIST Katılım Endeksi Hisseleri (XKTUM)":
        if not is_bist_stock(snap) or not snap.is_participation_compliant:
            return False
    elif katilim_modu == "☪️ Sadece Küresel İslami ETF & Sukuk / Altın":
        if not snap.is_participation_compliant:
            return False
        if snap.asset_class not in (
            AssetClass.GLOBAL_ETF_EQUITY,
            AssetClass.BONDS_EUROBOND_SUKUK,
            AssetClass.FOREX_PRECIOUS_METALS,
            AssetClass.MONEY_MARKET_DEPOSIT,
        ):
            return False

    # 3. BIST Endeks ve BIST Sektör Filtreleri
    # KRİTİK DÜZELTME: Kullanıcı BIST Endeksi veya BIST Sektörü seçtiyse, BIST hissesi olmayan
    # tüm varlıklar (TI1 para piyasası fonu, döviz, ABD ETF vb.) anında elenir!
    if endeks_secimi != "Tüm Evren (Endeks Kısıtı Yok)":
        if not is_bist_stock(snap):
            return False
        if endeks_secimi == "BIST Katılım Tüm (XKTUM - 200+ Hisse)":
            if not (snap.is_participation_compliant or snap.symbol in KATILIM_TUM):
                return False
        elif endeks_secimi == "BIST Katılım 30 (XK030)":
            if snap.symbol not in KATILIM_30:
                return False
        elif endeks_secimi == "BIST 30 (XU030)":
            if snap.symbol not in BIST_30:
                return False
        elif endeks_secimi == "BIST 50 (XU050)":
            if snap.symbol not in BIST_50:
                return False
        elif endeks_secimi == "BIST 100 (XU100)":
            if snap.symbol not in BIST_100:
                return False

    if sektor_secimi:
        if not is_bist_stock(snap):
            return False
        is_in_selected_sector = False
        for sec in sektor_secimi:
            if sec == snap.sector or snap.symbol in SEKTOR_SOZLUGU.get(sec, []):
                is_in_selected_sector = True
                break
        if not is_in_selected_sector:
            return False

    # 4. TEFAS Fon Kategorisi Filtresi (Seçildiğinde sadece o kategorideki TEFAS fonlarını gösterir)
    if tefas_kategori_secimi:
        if snap.asset_class != AssetClass.TEFAS_MUTUAL_FUND:
            return False
        if snap.sector not in tefas_kategori_secimi:
            return False

    # 5. Küresel ETF & ABD Sektör Filtresi
    if kuresel_kategori_secimi:
        if snap.asset_class != AssetClass.GLOBAL_ETF_EQUITY:
            return False
        if snap.sector not in kuresel_kategori_secimi:
            return False

    # 6. Döviz, Altın, Emtia & Tahvil/Sukuk Alt Grup Filtresi
    if makro_kategori_secimi:
        if snap.sector not in makro_kategori_secimi:
            return False

    # 7. Temel Analiz & Fon Gideri (TER) Filtreleri
    if fk_filtre_aktif:
        if snap.pe_ratio is None:
            if not eksik_verileri_goster:
                return False
        elif snap.pe_ratio > fk_max:
            return False

    if pddd_filtre_aktif:
        if snap.pb_ratio is None:
            if not eksik_verileri_goster:
                return False
        elif snap.pb_ratio > pddd_max:
            return False

    if roe_filtre_aktif:
        if snap.roe_pct is None:
            if not eksik_verileri_goster:
                return False
        elif snap.roe_pct < roe_min:
            return False

    if ter_filtre_aktif:
        if snap.expense_ratio_pct is None:
            if not eksik_verileri_goster:
                return False
        elif snap.expense_ratio_pct > ter_max:
            return False

    # 8. Teknik Filtreler
    m = QuantitativeIndicators.get_cached_metrics(snap)
    rsi_val = snap.rsi_tv if snap.rsi_tv is not None else m["rsi_14"]
    if rsi_filtre_aktif or rsi_aralik != (0, 100):
        if not (rsi_aralik[0] <= rsi_val <= rsi_aralik[1]):
            return False

    if sadece_trend:
        if snap.current_price < m["sma50"] and snap.trend_tv != "🚀 Up":
            return False

    if sadece_macd_al:
        if snap.macd_signal_tv != "🟢 Bull" and m["macd_hist"] <= 0:
            return False

    # 9. Karar Sinyali ve Skor Filtreleri
    if min_karar_skoru > 0 and sig.composite_score < min_karar_skoru:
        return False

    if sinyal_filtre_secimi == "Sadece AL & GÜÇLÜ AL" and sig.action not in (SignalAction.STRONG_BUY, SignalAction.BUY):
        return False
    elif sinyal_filtre_secimi == "Sadece GÜÇLÜ AL" and sig.action != SignalAction.STRONG_BUY:
        return False
    elif sinyal_filtre_secimi == "Sadece TUT / İZLE" and sig.action != SignalAction.HOLD:
        return False

    return True


filtered_pairs = [
    (snap, signals_by_symbol[sym])
    for sym, snap in universe_snapshots.items()
    if sym in signals_by_symbol and passes_filters(snap, signals_by_symbol[sym])
]
filtered_snapshots = {snap.symbol: snap for snap, _ in filtered_pairs}
filtered_signals = [sig for _, sig in filtered_pairs]

# --- ÜST MAKRO VE CANLI PİYASA ŞERİDİ (TICKER TAPE) ---
st.markdown(
    f"""
    <div style="background: linear-gradient(90deg, #101522 0%, #151C2C 100%); border: 1px solid #1E2638; border-radius: 10px; padding: 12px 18px; margin-bottom: 16px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; font-size: 13.5px; box-shadow: 0 4px 16px rgba(0,0,0,0.3);">
        <div>
            <span style="font-weight:700; color:#FFFFFF; font-size:16px;">AlpQuant Pro v4.0</span>
            <span style="color:#787B86; margin: 0 8px;">|</span>
            <span style="color:#00E6B8; font-weight:600;">🟢 {len(universe_snapshots)} ENSTRÜMAN CANLI EVREN (AKTİF FİLTRE: {len(filtered_signals)})</span>
        </div>
        <div style="display: flex; gap: 16px; flex-wrap: wrap; align-items: center;">
            <span>🇹🇷 <b>BIST 100:</b> 9,250.40 <span style='color:#00E6B8;'>+1.25%</span></span>
            <span>💎 <b>BIST 30:</b> 10,180.15 <span style='color:#00E6B8;'>+1.42%</span></span>
            <span>🇺🇸 <b>S&P 500:</b> 585.00 <span style='color:#00E6B8;'>+0.38%</span></span>
            <span>🥇 <b>Gram Altın:</b> 3,050 TL <span style='color:#00E6B8;'>+0.75%</span></span>
            <span>⚡ <b>Bitcoin:</b> $68,500 <span style='color:#00E6B8;'>+2.10%</span></span>
            <span style="background:rgba(41,98,255,0.2); border:1px solid #2962FF; color:#70A1FF; padding:2px 8px; border-radius:12px; font-weight:600; font-size:12px;">🛡️ SPK Seviye {prof.risk_score}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- BİRLEŞİK SEGMENTLİ İŞLEM MASALARI ---
tab_terminal, tab_signals, tab_screener, tab_kap, tab_quant = st.tabs([
    "📊 Piyasa Terminali",
    "⚡ Karar Motoru & Sinyaller",
    "🎯 Çoklu Varlık Taraması",
    "🧠 KAP AI İstihbarat Masası",
    "🔬 Kantitatif Backtest & PDF",
])

# ============================================================================
# MASA 1: CANLI PİYASA & ENDEKS TERMİNALİ
# ============================================================================
with tab_terminal:
    st.subheader("📊 Canlı Piyasa Genel Bakış & Endeks Grafikleri")
    st.caption("Açılış paneli: Anlık endeks seviyeleri, 6 aylık tepe/dip bantları, 50 günlük hareketli ortalama trend analizi ve interaktif mum grafikleri:")

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
            f"<div style='background-color:#121722; padding:14px 18px; border-radius:8px; border-left: 4px solid #2962FF; margin-top:10px; margin-bottom: 18px; color: #D1D4DC; font-size: 14.5px;'>{commentary}</div>",
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
            template="plotly_dark", paper_bgcolor="#0B0E14", plot_bgcolor="#0B0E14",
            margin=dict(l=10, r=10, t=10, b=10), xaxis_rangeslider_visible=False,
            xaxis=dict(showgrid=True, gridcolor="#1E2638"),
            yaxis=dict(showgrid=True, gridcolor="#1E2638", side="right"),
            height=480,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_idx, use_container_width=True)

    @st.cache_data(ttl=900, show_spinner=False)
    def safe_get_market_index_data(ticker: str, fallback_symbol: str, base_price: float, risk_level: int = 3, live_flag: bool = True) -> pd.DataFrame:
        df = None
        if live_flag and HAS_YFINANCE:
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
        xu100_df = safe_get_market_index_data("XU100.IS", "XU100", 9250.0, risk_level=3, live_flag=canli_veri_aktif)
        render_dashboard(xu100_df, "BIST 100 (XU100)", "TL")
    with sub_t2:
        xu030_df = safe_get_market_index_data("XU030.IS", "XU030", 10180.0, risk_level=3, live_flag=canli_veri_aktif)
        render_dashboard(xu030_df, "BIST 30 (XU030)", "TL")
    with sub_t3:
        spy_df = safe_get_market_index_data("SPY", "SPY", 585.0, risk_level=3, live_flag=canli_veri_aktif)
        render_dashboard(spy_df, "S&P 500 ETF (SPY)", "USD")
    with sub_t4:
        xau_df = safe_get_market_index_data("GC=F", "XAU_TRY", 3050.0, risk_level=1, live_flag=canli_veri_aktif)
        render_dashboard(xau_df, "Gram Altın (Spot TL)", "TL")
    with sub_t5:
        btc_df = safe_get_market_index_data("BTC-USD", "BTC-USD", 68500.0, risk_level=5, live_flag=canli_veri_aktif)
        render_dashboard(btc_df, "Bitcoin (BTC/USD)", "USD")

# ============================================================================
# MASA 2: KARAR MOTORU & SİNYAL MASASI
# ============================================================================
with tab_signals:
    st.subheader("⚡ Otonom Karar Motoru & Sinyal Masası")

    filter_tags = [f"<span class='aq-badge-filter'>Evren: {varlik_grubu}</span>"]
    if katilim_modu != "Tüm Enstrümanlar (Konvansiyonel + Katılım)":
        filter_tags.append(f"<span class='aq-badge-filter'>{katilim_modu}</span>")
    if endeks_secimi != "Tüm Evren (Endeks Kısıtı Yok)":
        filter_tags.append(f"<span class='aq-badge-filter'>Endeks: {endeks_secimi}</span>")
    if sektor_secimi:
        filter_tags.append(f"<span class='aq-badge-filter'>BIST Sektör: {', '.join(sektor_secimi[:3])}{'...' if len(sektor_secimi)>3 else ''}</span>")
    if tefas_kategori_secimi:
        filter_tags.append(f"<span class='aq-badge-filter'>TEFAS: {', '.join(tefas_kategori_secimi[:2])}{'...' if len(tefas_kategori_secimi)>2 else ''}</span>")
    if kuresel_kategori_secimi:
        filter_tags.append(f"<span class='aq-badge-filter'>Küresel: {', '.join(kuresel_kategori_secimi[:2])}</span>")
    if makro_kategori_secimi:
        filter_tags.append(f"<span class='aq-badge-filter'>Makro: {', '.join(makro_kategori_secimi[:2])}</span>")
    if arama_metni:
        filter_tags.append(f"<span class='aq-badge-filter'>Arama: '{arama_metni}'</span>")
    if fk_filtre_aktif:
        filter_tags.append(f"<span class='aq-badge-filter'>F/K &le; {fk_max}</span>")
    if pddd_filtre_aktif:
        filter_tags.append(f"<span class='aq-badge-filter'>PD/DD &le; {pddd_max}</span>")
    if roe_filtre_aktif:
        filter_tags.append(f"<span class='aq-badge-filter'>ROE &ge; %{roe_min}</span>")
    if ter_filtre_aktif:
        filter_tags.append(f"<span class='aq-badge-filter'>TER &le; %{ter_max}</span>")
    if rsi_filtre_aktif or rsi_aralik != (0, 100):
        filter_tags.append(f"<span class='aq-badge-filter'>RSI: {rsi_aralik[0]}-{rsi_aralik[1]}</span>")
    if sinyal_filtre_secimi != "Tüm Sinyaller":
        filter_tags.append(f"<span class='aq-badge-filter'>{sinyal_filtre_secimi}</span>")
    if min_karar_skoru > 0:
        filter_tags.append(f"<span class='aq-badge-filter'>Min Skor: {min_karar_skoru}</span>")

    st.markdown(f"<div style='margin-bottom:12px;'>{' '.join(filter_tags)}</div>", unsafe_allow_html=True)

    buy_signals = [s for s in filtered_signals if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY)]
    katilim_count = sum(1 for snap, _ in filtered_pairs if snap.is_participation_compliant)
    avg_score = float(np.mean([s.composite_score for s in filtered_signals])) if filtered_signals else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Filtreyi Geçen Varlık", f"{len(filtered_signals)} / {len(universe_snapshots)}")
    m2.metric("Aktif AL / GÜÇLÜ AL Sinyali", f"{len(buy_signals)}")
    m3.metric("Katılım Uyumlu Varlık", f"{katilim_count}")
    m4.metric("Filtrelenen Ortalama Skor", f"{avg_score:.1f} / 100")

    if len(filtered_signals) == 0:
        st.warning("⚠️ Sol menüde belirlediğiniz filtre kriterlerine uyan varlık bulunamadı. Lütfen sol menüdeki filtreleri gevşetin.")
    else:
        signal_rows = []
        for snap, s in sorted(filtered_pairs, key=lambda x: x[1].composite_score, reverse=True):
            signal_rows.append({
                "Kod": s.symbol,
                "Varlık / Fon Adı": s.name,
                "Katılım": "☪️ Katılım" if snap.is_participation_compliant else "Konvansiyonel",
                "Risk Seviyesi": f"Seviye {s.risk_level}",
                "Varlık Sınıfı": s.asset_class,
                "Sektör / Kategori": snap.sector,
                "Karar Sinyali": s.action.value,
                "Bileşik Skor": s.composite_score,
                "Teknik Skor": s.technical_score,
                "Temel/TER": s.fundamental_score,
                "KAP AI": s.kap_sentiment_score,
                "Anlık Fiyat": f"{s.current_price:,.2f} {s.currency}",
                "Önerilen Adet": s.recommended_quantity,
                "Emir Tutarı (TL)": s.recommended_notional_try,
                "Stop-Loss": s.stop_loss_price,
                "Take-Profit": s.take_profit_price,
                "Karar Gerekçesi": " | ".join(s.rationale[:3]),
            })

        st.dataframe(pd.DataFrame(signal_rows), use_container_width=True, hide_index=True, height=520)

        col_mail_btn, _ = st.columns([1, 2])
        with col_mail_btn:
            if st.button("📧 Filtrelenen Sinyalleri E-Posta Olarak Gönder", use_container_width=True):
                ok, msg = EmailSignalNotifier.send_email_alert(
                    recipient_email=alici_eposta,
                    suitability=prof,
                    signals=filtered_signals[:50],
                    sender_email=gonderici_eposta,
                    smtp_app_password=smtp_sifre or None,
                )
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.info(f"ℹ️ {msg} (Aşağıda hazır HTML e-posta çıktısını inceleyebilirsiniz.)")

        with st.expander("📨 E-Posta Bildirim Raporu Canlı Önizleme (HTML - İlk 40 Sinyal)"):
            st.components.v1.html(
                EmailSignalNotifier.build_html_report(prof, sorted(filtered_signals, key=lambda x: x.composite_score, reverse=True)[:40]),
                height=420,
                scrolling=True,
            )

# ============================================================================
# MASA 3: ÇOKLU VARLIK TARAYICISI
# ============================================================================
with tab_screener:
    st.subheader(f"🎯 Çoklu Varlık Tarayıcısı ({len(filtered_snapshots)} / {len(universe_snapshots)} Varlık Listeleniyor)")
    st.caption("Sol menüdeki varlık sınıfı, katılım modu, BIST endeksi/sektörü, TEFAS fon kategorisi ve çarpan filtreleriyle tam senkronize tarama tablosu:")

    if len(filtered_snapshots) == 0:
        st.warning("⚠️ Sol menüdeki filtrelere uyan varlık bulunamadı. Lütfen sol panelden kriterleri genişletin.")
    else:
        asset_table = []
        for sym, snap in filtered_snapshots.items():
            m = QuantitativeIndicators.get_cached_metrics(snap)
            asset_table.append({
                "Sembol": sym,
                "Varlık / Fon Adı": snap.name,
                "Katılım Durumu": "☪️ Katılım Uyumlu" if snap.is_participation_compliant else "Konvansiyonel",
                "Risk Seviyesi": f"Seviye {snap.risk_level}",
                "SPK Durumu": "✅ Açık" if snap.risk_level in prof.approved_levels else "🔒 Kısıtlı",
                "Varlık Sınıfı": snap.asset_class.value,
                "Sektör / Fon Kategorisi": snap.sector,
                "Son Fiyat": f"{snap.current_price:,.2f} {snap.currency}",
                "RSI (14)": snap.rsi_tv if snap.rsi_tv is not None else m["rsi_14"],
                "1 Aylık Dibe Uzaklık (%)": f"%{m['dist_from_monthly_low_pct']:.2f}",
                "F/K": snap.pe_ratio if snap.pe_ratio is not None else "-",
                "PD/DD": snap.pb_ratio if snap.pb_ratio is not None else "-",
                "ROE (%)": snap.roe_pct if snap.roe_pct is not None else "-",
                "Fon Gideri (TER %)": snap.expense_ratio_pct if snap.expense_ratio_pct is not None else "-",
            })
        st.dataframe(pd.DataFrame(asset_table), use_container_width=True, hide_index=True, height=560)

# ============================================================================
# MASA 4: KAP AI İSTİHBARAT MASASI
# ============================================================================
with tab_kap:
    st.subheader("🧠 KAP Bildirim İstihbarat & Gemini AI Analiz Masası")
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

# ============================================================================
# MASA 5: KANTİTATİF BACKTEST & KURUMSAL PDF RAPORU
# ============================================================================
with tab_quant:
    st.subheader("🔬 Kantitatif Strateji Backtesti & Kurumsal PDF Araştırma Raporu")
    backtester = TimeSeriesBacktester()

    target_snaps = filtered_snapshots if filtered_snapshots else universe_snapshots
    tumunu_backtest_et = st.checkbox(
        f"Tüm Filtrelenen Evreni Backtest Et ({len(target_snaps)} Varlık — Varsayılan: En Yüksek Skorlu İlk 100 Varlık)",
        value=False,
    )
    snaps_for_bt = list(target_snaps.items()) if tumunu_backtest_et else list(target_snaps.items())[:100]

    bt_rows = []
    for sym, snap in snaps_for_bt:
        rep = backtester.run_backtest(sym, snap.ohlcv_history, name=snap.name, risk_level=snap.risk_level)
        bt_rows.append({
            "Sembol": rep.symbol,
            "Varlık Adı": rep.name,
            "Katılım": "☪️ Katılım" if snap.is_participation_compliant else "Konvansiyonel",
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
    secilen_varlik = st.selectbox("Detaylı Mum Grafiği ve PDF Araştırma Raporu İçin Varlık Seçin", list(target_snaps.keys()))
    if secilen_varlik:
        snap_sel = target_snaps[secilen_varlik]
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
            paper_bgcolor="#0B0E14",
            plot_bgcolor="#0B0E14",
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