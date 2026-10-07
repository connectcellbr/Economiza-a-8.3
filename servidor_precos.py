#!/usr/bin/env python3
"""
Economiza AI — Servidor local de preços reais
======================================
Roda a busca no Zoom e devolve JSON para o painel HTML.

Uso:
  python servidor_precos.py

Depois abra o painel: http://127.0.0.1:8765/
"""

from __future__ import annotations

import json
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from auto_aprendizado import record_event, update_metric, snapshot, learn, propose_improvements, get_status
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

DEFAULT_PORT = 8765
PORT = int(__import__("os").environ.get("PORT", __import__("os").environ.get("PRECO_PORT", DEFAULT_PORT)))
DIR = Path(__file__).resolve().parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_price(price_str: str) -> Optional[float]:
    if not price_str:
        return None
    cleaned = re.sub(r"[R$\s]", "", price_str)
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_installments(text: str) -> str:
    m = re.search(r"(\d+\s*x\s*de\s*R\$\s*[\d.,]+)(?:\s*sem\s*juros)?", text, re.I)
    if m:
        result = m.group(0).strip()
        if "sem juros" in text.lower() and "sem juros" not in result.lower():
            result += " sem juros"
        return result
    return ""


def extract_cashback(text: str) -> str:
    m = re.search(r"(\d+%\s*de\s*volta)", text, re.I)
    if m:
        return m.group(1)
    if "cashback" in text.lower():
        return "Cashback disponível"
    return ""


def is_new_product(name: str, text: str) -> bool:
    lower = (name + " " + text).lower()
    return not any(w in lower for w in ["usado", "seminovo", "recondicionado", "open box"])


def search_zoom(product: str, max_results: int = 15) -> List[Dict[str, Any]]:
    query = urllib.parse.quote_plus(product)
    url = f"https://www.zoom.com.br/search?q={query}"

    try:
        response = requests.get(url, headers=HEADERS, timeout=8)
        response.raise_for_status()
    except requests.RequestException as e:
        return {"error": str(e), "results": []}  # type: ignore

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select('article[class*="OrqProductCard"]')

    results: List[Dict[str, Any]] = []
    keywords = [k.lower() for k in product.split() if len(k) > 2]

    for card in cards[: max_results * 3]:
        full_text = card.get_text(separator=" | ", strip=True)

        name_el = card.select_one('[class*="Name_OrqProductCard_Name"]')
        name = name_el.get_text(strip=True) if name_el else "Nome não encontrado"

        price_text = None
        for s in card.find_all(string=re.compile(r"^R\$\s*[\d.,]+$")):
            parent_text = s.parent.get_text() if s.parent else ""
            if "x de" in parent_text.lower():
                continue
            price_text = s.strip()
            break
        if not price_text:
            matches = re.findall(r"R\$\s*[\d.,]+", full_text)
            for m in matches:
                if "x de" not in full_text.lower().split(m)[0][-25:]:
                    price_text = m
                    break

        price_value = parse_price(price_text) if price_text else None
        if price_value is None:
            continue

        store = "—"
        via = card.find(string=re.compile(r"Via\s+.+"))
        if via:
            store = re.sub(r"^Via\s+", "", via.strip()).strip()
            store = re.split(r"\s{2,}|\|", store)[0].strip()

        link_el = card.select_one("a[href]")
        href = link_el.get("href", "") if link_el else ""
        link = ("https://www.zoom.com.br" + href) if href.startswith("/") else href

        rating = "—"
        rating_match = re.search(r"(\d(?:\.\d)?)\s*\(\d+\)", full_text)
        if rating_match:
            rating = rating_match.group(1)

        installments = extract_installments(full_text)
        cashback = extract_cashback(full_text)

        shipping_text = "A calcular"
        shipping_cost = None
        if "frete grátis" in full_text.lower() or "frete gratis" in full_text.lower():
            shipping_text = "Grátis"
            shipping_cost = 0.0

        is_new = is_new_product(name, full_text)

        name_lower = name.lower()
        relevance = sum(1 for k in keywords if k in name_lower)
        main_phrase = " ".join(keywords[:2]) if len(keywords) >= 2 else (keywords[0] if keywords else "")
        if main_phrase and main_phrase in name_lower:
            relevance += 3
        if is_new:
            relevance += 1

        results.append(
            {
                "name": name,
                "price": price_value,
                "priceText": price_text or "—",
                "store": store,
                "link": link or url,
                "rating": rating,
                "isNew": is_new,
                "installments": installments,
                "cashback": cashback,
                "shippingText": shipping_text,
                "shippingCost": shipping_cost,
                "freeShip": shipping_cost == 0,
                "_relevance": relevance,
            }
        )

    # Filtro de relevância
    if keywords:
        important = keywords[:2] if len(keywords) >= 2 else keywords
        filtered = [r for r in results if all(k in r["name"].lower() for k in important)]
        if len(filtered) >= 2:
            results = filtered
        else:
            min_rel = max(1, (len(keywords) + 1) // 2)
            results = [r for r in results if r.get("_relevance", 0) >= min_rel]

    results.sort(key=lambda o: (-o.get("_relevance", 0), o["price"]))
    for r in results:
        r.pop("_relevance", None)

    return results[:max_results]




# ================= PESQUISA WEB GERAL =================
def _clean_text(text: str) -> str:
    return re.sub(r"\\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)).strip()


def _search_html_engine(url: str, source: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Consulta um mecanismo público de busca com tratamento uniforme."""
    r = requests.get(url, headers=HEADERS, timeout=4, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []

    if source == "DuckDuckGo":
        items = soup.select(".result")
        for item in items[:max_results]:
            a = item.select_one(".result__a")
            snippet_el = item.select_one(".result__snippet")
            if not a:
                continue
            out.append({
                "title": a.get_text(" ", strip=True),
                "url": a.get("href", ""),
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                "source": source,
            })
    elif source == "Bing":
        items = soup.select("li.b_algo")
        for item in items[:max_results]:
            a = item.select_one("h2 a")
            snippet_el = item.select_one(".b_caption p")
            if not a:
                continue
            out.append({
                "title": a.get_text(" ", strip=True),
                "url": a.get("href", ""),
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                "source": source,
            })
    elif source == "Google":
        # Estrutura simples e tolerante; não depende de classes que mudam com frequência.
        for a in soup.select("a"):
            href = a.get("href", "")
            title = a.get_text(" ", strip=True)
            if not href.startswith("http") or not title or len(title) < 4:
                continue
            host = urllib.parse.urlparse(href).netloc.lower()
            if "google." in host:
                continue
            out.append({"title": title, "url": href, "snippet": "", "source": source})
            if len(out) >= max_results:
                break
    elif source == "Yahoo":
        for item in soup.select("div.algo")[:max_results]:
            a = item.select_one("h3 a, h3.title a, a")
            if not a:
                continue
            href = a.get("href", "")
            title = a.get_text(" ", strip=True)
            snippet_el = item.select_one("div.compText, p")
            if href and title:
                out.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                    "source": source,
                })
    return [x for x in out if x.get("url")]


def search_duckduckgo(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    return _search_html_engine(url, "DuckDuckGo", max_results)


def search_bing(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
    return _search_html_engine(url, "Bing", max_results)


def search_google(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    url = "https://www.google.com/search?hl=pt-BR&num=10&q=" + urllib.parse.quote_plus(query)
    return _search_html_engine(url, "Google", max_results)


def search_yahoo(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    url = "https://search.yahoo.com/search?p=" + urllib.parse.quote_plus(query)
    return _search_html_engine(url, "Yahoo", max_results)


def web_search(query: str, max_results: int = 12) -> Dict[str, Any]:
    """Pesquisa em TODOS os mecanismos configurados e consolida os resultados.

    Importante: max_results é por mecanismo, não um limite global. Assim Google,
    Bing, DuckDuckGo e Yahoo têm oportunidade real de contribuir para a comparação.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    engine_fns = (search_google, search_bing, search_duckduckgo, search_yahoo)
    raw_results: List[Dict[str, Any]] = []
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn, query, max_results): fn.__name__ for fn in engine_fns}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                raw_results.extend(fut.result() or [])
            except Exception as exc:
                errors.append(f"{name}: {exc}")

    # Deduplica somente URLs idênticas; resultados equivalentes de mecanismos
    # diferentes permanecem rastreáveis através de `searchEngines`.
    by_url: Dict[str, Dict[str, Any]] = {}
    for item in raw_results:
        raw = item.get("url", "")
        key = raw.split("#", 1)[0].rstrip("/")
        if not key:
            continue
        if key not in by_url:
            by_url[key] = dict(item)
        else:
            old = by_url[key]
            engines = set(old.get("searchEngines", []))
            if old.get("source"):
                engines.add(old["source"])
            if item.get("source"):
                engines.add(item["source"])
            old["searchEngines"] = sorted(engines)

    results = list(by_url.values())
    return {
        "ok": bool(results) or any("Mercado Livre API" not in e for e in errors),
        "query": query,
        "count": len(results),
        "results": results,
        "engines": sorted(set(x.get("source") for x in raw_results if x.get("source"))),
        "errors": errors,
    }


def _domain_store(url: str) -> str:
    # Resolve redirects Yahoo/Bing (RU= / u=) para a loja real.
    raw = url or ""
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        for key in ("RU", "u", "url", "q"):
            if key in qs and qs[key]:
                cand = qs[key][0]
                if cand.startswith("http"):
                    raw = cand
                    break
                # RU às vezes vem percent-encoded
                decoded = urllib.parse.unquote(cand)
                if decoded.startswith("http"):
                    raw = decoded
                    break
    except Exception:
        pass
    host = urllib.parse.urlparse(raw).netloc.lower().replace("www.", "")
    # Ignora hosts de motores de busca
    if any(x in host for x in ("search.yahoo.", "google.", "bing.", "duckduckgo.")):
        return ""
    mapping = {
        "mercadolivre.com.br": "Mercado Livre",
        "produto.mercadolivre.com.br": "Mercado Livre",
        "amazon.com.br": "Amazon Brasil",
        "magazineluiza.com.br": "Magazine Luiza",
        "magalu.com": "Magazine Luiza",
        "casasbahia.com.br": "Casas Bahia",
        "kabum.com.br": "KaBuM!",
        "shopee.com.br": "Shopee",
        "carrefour.com.br": "Carrefour",
        "extra.com.br": "Extra",
        "pontofrio.com.br": "Ponto",
        "americanas.com.br": "Americanas",
        "fastshop.com.br": "Fast Shop",
        "pichau.com.br": "Pichau",
        "aliexpress.com": "AliExpress",
        "dell.com": "Dell",
        "samsung.com": "Samsung",
        "apple.com": "Apple",
        "lenovo.com": "Lenovo",
        "acer.com": "Acer",
        "asus.com": "ASUS",
        "zoom.com.br": "Zoom",
    }
    for domain, name in mapping.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host



def _is_exact_product_url(store: str, url: str) -> bool:
    """Aceita somente URLs que aparentam ser a página unitária do produto.
    Nunca usa busca/categoria/listagem como destino final do comprador.
    """
    if not url or not store:
        return False
    raw = url.strip()
    try:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.lower().rstrip("/")
        query = parsed.query.lower()
    except Exception:
        return False
    if not path or path in ("", "/"):
        return False
    # Padrões de páginas de busca/listagem/categoria que jamais devem virar destino.
    blocked = ("/busca", "/search", "/lista", "/categoria", "/categorias",
               "/departamento", "/departamentos", "/ofertas", "/s?", "/catalog")
    if any(x in path for x in blocked):
        return False
    if store == "Mercado Livre":
        return bool(re.search(r"/(mlb|mlb-)[0-9]+", path))
    if store == "Amazon Brasil":
        return bool(re.search(r"/(dp|gp/product)/[a-z0-9]{8,}", path))
    if store == "Magazine Luiza":
        return bool(re.search(r"/[^/]+/p/[a-z0-9-]+", path) or re.search(r"/produto/[a-z0-9-]+", path))
    if store == "KaBuM!":
        return bool(re.search(r"/produto/[0-9]+/", path)) or bool(re.search(r"/[a-z0-9-]+-[0-9]+\.html", path))
    if store == "Casas Bahia":
        return bool(re.search(r"/[^/]+/p/[a-z0-9-]+", path) or re.search(r"/produto", path))
    if store == "Americanas":
        return bool(re.search(r"/produto/[a-z0-9-]+", path) or re.search(r"/[^/]+/p/[0-9]+", path))
    if store == "Shopee":
        return bool(re.search(r"/product/[0-9]+/[0-9]+", path))
    if store == "Carrefour":
        return bool(re.search(r"/[^/]+/p/[a-z0-9-]+", path) or re.search(r"/produto", path))
    if store == "Ponto":
        return bool(re.search(r"/[^/]+/p/[a-z0-9-]+", path) or re.search(r"/produto", path))
    if store == "Fast Shop":
        return bool(re.search(r"/[^/]+/p/[a-z0-9-]+", path) or re.search(r"/produto", path))
    if store == "Pichau":
        return bool(re.search(r"/[a-z0-9-]+-[a-z0-9]+$", path)) and len(path.split("/")) >= 2
    return bool(path.count("/") >= 2 and not query.startswith(("q=", "keyword=", "search=")))

def _prices_from_text(text: str) -> List[float]:
    vals = []
    for m in re.finditer(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?|[0-9]+(?:,[0-9]{2})?)", text or "", re.I):
        prefix = (text[max(0, m.start()-24):m.start()] or "").lower()
        # Não confundir parcela (ex.: 12x de R$ 477,67) com preço à vista.
        if re.search(r"\d+\s*x\s*de\s*$", prefix):
            continue
        try:
            vals.append(float(m.group(1).replace('.', '').replace(',', '.')))
        except ValueError:
            pass
    return vals


def _score_product_match(query: str, title: str) -> int:
    q = [x.lower() for x in re.findall(r"[a-z0-9]+", query) if len(x) > 2]
    t = (title or '').lower()
    return sum(1 for x in q if x in t)




def search_mercadolivre_api(product: str, max_results: int = 24) -> tuple[List[Dict[str, Any]], List[str]]:
    """Fonte direta e gratuita do Mercado Livre, usada como fallback robusto.

    Ela não depende de Google/Bing/DuckDuckGo/Yahoo e por isso continua funcionando
    quando mecanismos de busca bloqueiam requisições vindas do Render.
    """
    url = "https://api.mercadolibre.com/sites/MLB/search?" + urllib.parse.urlencode({
        "q": product,
        "limit": min(max_results, 50),
        "sort": "relevance",
    })
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=8)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return [], [f"Mercado Livre API: {exc}"]

    out: List[Dict[str, Any]] = []
    for item in data.get("results", []) or []:
        title = (item.get("title") or "").strip()
        price = item.get("price")
        if not title or not isinstance(price, (int, float)) or price <= 0:
            continue
        permalink = item.get("permalink") or ""
        if not permalink:
            continue
        shipping = item.get("shipping") or {}
        free_ship = bool(shipping.get("free_shipping"))
        condition = (item.get("condition") or "new").lower()
        out.append({
            "name": title,
            "price": float(price),
            "priceText": f"R$ {float(price):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "store": "Mercado Livre",
            "link": permalink,
            "rating": "—",
            "isNew": condition == "new",
            "installments": "",
            "cashback": "",
            "shippingText": "Grátis" if free_ship else "A calcular",
            "shippingCost": 0.0 if free_ship else None,
            "freeShip": free_ship,
            "source": "Mercado Livre API",
            "searchEngines": ["Mercado Livre API"],
            "_relevance": _score_product_match(product, title),
        })
    return out, []



def search_mercadolivre_html(product: str, max_results: int = 18) -> tuple[List[Dict[str, Any]], List[str]]:
    """Fallback sem API: lê a página pública de resultados do Mercado Livre.

    Serve como segunda rota quando a API pública estiver indisponível no Render.
    Só aceita ofertas que tenham preço explícito e URL de produto.
    """
    url = "https://lista.mercadolivre.com.br/" + urllib.parse.quote(product.replace(" ", "-"))
    try:
        r = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        return [], [f"Mercado Livre web: {exc}"]

    out: List[Dict[str, Any]] = []
    cards = soup.select("li.ui-search-layout__item") or soup.select("div.ui-search-result__content")
    for card in cards[:max_results]:
        title_el = card.select_one("h2.ui-search-item__title, h2.poly-component__title, a.ui-search-link")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            continue
        link_el = card.select_one("a.ui-search-link, a.poly-component__title")
        link = link_el.get("href", "") if link_el else ""
        if not link:
            continue
        price = None
        meta = card.select_one('[itemprop="price"]')
        if meta and meta.get("content"):
            try:
                price = float(meta.get("content"))
            except (TypeError, ValueError):
                price = None
        if price is None:
            amount = card.select_one(".andes-money-amount")
            text = amount.get_text(" ", strip=True) if amount else card.get_text(" ", strip=True)
            vals = _prices_from_text(text)
            if vals:
                price = min(vals)
        if not price or price <= 0:
            continue
        shipping = card.get_text(" ", strip=True).lower()
        free_ship = "frete grátis" in shipping or "frete gratis" in shipping
        out.append({
            "name": title,
            "price": float(price),
            "priceText": f"R$ {float(price):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "store": "Mercado Livre",
            "link": link,
            "rating": "—",
            "isNew": True,
            "installments": extract_installments(card.get_text(" ", strip=True)),
            "cashback": extract_cashback(card.get_text(" ", strip=True)),
            "shippingText": "Grátis" if free_ship else "A calcular",
            "shippingCost": 0.0 if free_ship else None,
            "freeShip": free_ship,
            "source": "Mercado Livre web",
            "searchEngines": ["Mercado Livre web"],
            "_relevance": _score_product_match(product, title),
        })
    return out, []

def _from_web_item_fast(product: str, store: str, item: dict) -> Optional[Dict[str, Any]]:
    """Converte um resultado web em oferta sem exigir uma página perfeita."""
    title = (item.get("title") or "").strip()
    snippet = (item.get("snippet") or "").strip()
    if not title:
        return None
    text = f"{title} {snippet}"
    prices = [x for x in _prices_from_text(text) if x > 0]
    if not prices:
        return None
    relevance = _score_product_match(product, text)
    if relevance <= 0:
        return None
    engines = item.get("searchEngines") or ([item.get("source")] if item.get("source") else [])
    price = min(prices)
    return {
        "name": title,
        "price": price,
        "priceText": f"R$ {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "store": store,
        "link": item.get("url", ""),
        "rating": "—",
        "isNew": is_new_product(title, text),
        "installments": extract_installments(text),
        "cashback": extract_cashback(text),
        "shippingText": "A calcular",
        "shippingCost": None,
        "freeShip": False,
        "source": ", ".join(sorted(set(engines))) or "web",
        "searchEngines": sorted(set(engines)),
        "_relevance": relevance,
    }


def search_multifonte(product: str, max_results: int = 60) -> Dict[str, Any]:
    """Pesquisa rápida e robusta para ambientes como Render.

    A versão anterior abria uma busca em 7 lojas * 4 mecanismos de busca,
    chegando a dezenas de requisições simultâneas. No plano gratuito do Render
    isso podia ultrapassar o timeout do proxy e o navegador recebia 502.

    Agora o caminho principal é:
      1. Mercado Livre API direta.
      2. Zoom em paralelo como segunda fonte.
      3. Uma única busca ampla nos quatro mecanismos.

    As três tarefas rodam em paralelo, mas não criam dezenas de subtarefas.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    collected: List[Dict[str, Any]] = []
    errors: List[str] = []

    def do_ml():
        errors_local = []
        try:
            items, errs = search_mercadolivre_api(product, min(max_results, 30))
            errors_local.extend(errs or [])
            if items:
                return items, errors_local
        except Exception as exc:
            errors_local.append(f"Mercado Livre API: {exc}")
        try:
            items, errs = search_mercadolivre_html(product, min(max_results, 18))
            errors_local.extend(errs or [])
            return items, errors_local
        except Exception as exc:
            errors_local.append(f"Mercado Livre web: {exc}")
            return [], errors_local

    def do_zoom():
        try:
            zoom = search_zoom(product, max_results=12)
            if not isinstance(zoom, list):
                msg = zoom.get("error", "Zoom sem resultados") if isinstance(zoom, dict) else "Zoom sem resultados"
                return [], [str(msg)]
            out = []
            for item in zoom:
                item = dict(item)
                item["store"] = item.get("store") or "Zoom"
                item["source"] = "Zoom"
                item["searchEngines"] = ["Zoom"]
                item["_relevance"] = _score_product_match(product, item.get("name", "")) + 2
                if item["_relevance"] > 0 and item.get("price", 0) > 0:
                    out.append(item)
            return out, []
        except Exception as exc:
            return [], [f"Zoom: {exc}"]

    def do_broad():
        try:
            data = web_search(f"{product} preço comprar", max_results=6)
            out = []
            for item in data.get("results", []):
                store = _domain_store(item.get("url", ""))
                if not store or store == "Zoom":
                    continue
                parsed = _from_web_item_fast(product, store, item)
                if parsed:
                    out.append(parsed)
            return out, data.get("errors", [])
        except Exception as exc:
            return [], [f"busca ampla: {exc}"]

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(do_ml), ex.submit(do_zoom), ex.submit(do_broad)]
        for f in as_completed(futures):
            try:
                items, errs = f.result()
                collected.extend(items or [])
                errors.extend(errs or [])
            except Exception as exc:
                errors.append(str(exc))

    grouped: Dict[tuple, Dict[str, Any]] = {}
    for item in collected:
        normalized = re.sub(r"[^a-z0-9]+", "", item.get("name", "").lower())[:140]
        key = (item.get("store", "").lower(), normalized)
        old = grouped.get(key)
        if old is None or float(item.get("price", float("inf"))) < float(old.get("price", float("inf"))):
            grouped[key] = item
        elif old is not None:
            old["searchEngines"] = sorted(set(old.get("searchEngines", [])) | set(item.get("searchEngines", [])))

    results = _replace_zoom_links_with_store_links(list(grouped.values()), product)

    by_store: Dict[str, Dict[str, Any]] = {}
    for item in results:
        store = item.get("store") or "Outra"
        if store == "Zoom":
            continue
        old = by_store.get(store)
        if old is None or item.get("price", float("inf")) < old.get("price", float("inf")):
            by_store[store] = item

    results = sorted(by_store.values(), key=lambda x: x.get("price", float("inf")))
    for item in results:
        item.pop("_relevance", None)

    return {
        "ok": bool(results),
        "query": product,
        "count": len(results),
        "results": results[:max_results],
        "stores": sorted(by_store.keys()),
        "searchedStores": sorted(by_store.keys()) or ["Mercado Livre", "Zoom", "outras lojas via busca ampla"],
        "searchEngines": ["Mercado Livre API", "Google", "Bing", "DuckDuckGo", "Yahoo", "Zoom"],
        "coverage": {
            "engines": 5,
            "stores": len(by_store),
            "zoom": True,
            "broadSearch": True,
            "strategy": "api_direta_mais_busca_ampla_com_timeout_seguro_para_render",
        },
        "errors": errors[:20],
    }

STORE_SEARCH_URLS = {
    "Mercado Livre": "https://lista.mercadolivre.com.br/",
    "Amazon Brasil": "https://www.amazon.com.br/s?k=",
    "Magazine Luiza": "https://www.magazineluiza.com.br/busca/",
    "KaBuM!": "https://www.kabum.com.br/busca/",
    "Casas Bahia": "https://www.casasbahia.com.br/",
    "Americanas": "https://www.americanas.com.br/busca/",
    "Shopee": "https://shopee.com.br/search?keyword=",
    "Carrefour": "https://www.carrefour.com.br/busca/",
    "Ponto": "https://www.pontofrio.com.br/",
    "Fast Shop": "https://www.fastshop.com.br/web/s/",
    "Pichau": "https://www.pichau.com.br/search?q=",
}


def _product_tokens(text: str) -> List[str]:
    return [x for x in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(x) > 2]


def _direct_store_link(store: str, product: str) -> str:
    base = STORE_SEARCH_URLS.get(store)
    if not base:
        return ""
    return base + urllib.parse.quote_plus(product)


def _replace_zoom_links_with_store_links(results: List[Dict[str, Any]], product: str) -> List[Dict[str, Any]]:
    """Usa o Zoom somente para pesquisa; os cliques finais nunca levam ao Zoom."""
    direct = [x for x in results if x.get("source") != "Zoom" and _domain_store(x.get("link", "")) not in ("", "Zoom")]
    for item in results:
        if item.get("source") != "Zoom":
            continue
        store = item.get("store", "")
        name = item.get("name", "")
        candidates = [x for x in direct if x.get("store") == store and x.get("link")]
        qtokens = set(_product_tokens(product + " " + name))
        best = None
        best_score = 0
        for candidate in candidates:
            ctokens = set(_product_tokens(candidate.get("name", "")))
            overlap = len(qtokens & ctokens)
            score = overlap
            if candidate.get("price") and item.get("price"):
                ratio = abs(float(candidate["price"]) - float(item["price"])) / max(float(item["price"]), 1)
                if ratio <= 0.05:
                    score += 3
                elif ratio <= 0.15:
                    score += 1
            if score > best_score:
                best_score = score
                best = candidate
        if best and best_score >= max(3, min(5, len(qtokens))):
            item["link"] = best["link"]
            item["directStore"] = True
        else:
            # Sem página unitária comprovadamente correspondente, descarta o resultado.
            # Nunca mandamos o comprador para uma busca/listagem da loja.
            item["link"] = ""
            item["directStore"] = False
    return [x for x in results if x.get("link") and _domain_store(x.get("link", "")) != "Zoom"]


def escolher_melhor(results: List[Dict]) -> Optional[Dict]:
    if not results:
        return None

    def score(o: Dict) -> float:
        s = float(o["price"])
        if not o.get("isNew", True):
            s += 80
        if o.get("shippingCost") is None:
            s += 8
        if o.get("shippingCost") == 0:
            s -= 5
        if o.get("cashback"):
            s -= 3
        inst = (o.get("installments") or "").lower()
        if "sem juros" in inst:
            s -= 3
        return s

    return min(results, key=score)



def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def infer_intent(question: str) -> Dict[str, Any]:
    q = _norm(question).lower()
    shopping = bool(re.search(
        r"\b(pre[cç]o|comprar|compra|oferta|promo[cç][aã]o|barato|mais barato|quanto custa|onde comprar|"
        r"frete|parcel|produto|celular|iphone|samsung|notebook|fone|tv|geladeira|air fryer|"
        r"custo.?benef[ií]cio|vale a pena|desconto|loja|magalu|amazon|mercado\s*livre)\b",
        q,
    ))
    current = bool(re.search(r"\b(hoje|agora|atual|atualmente|esta semana|2026|2025|not[ií]cia|lan[cç]amento|vers[aã]o nova)\b", q))
    howto = bool(re.search(r"\b(como|passo a passo|ensine|tutorial|configurar|instalar|resolver|consertar)\b", q))
    compare = bool(re.search(r"\b(compar|diferen[cç]a|melhor|qual escolher|qual vale|versus|vs\.?|entre|custo.?benef)\b", q))
    return {"shopping": shopping, "current": current, "howto": howto, "compare": compare}


def build_plan(question: str) -> List[Dict[str, Any]]:
    intent = infer_intent(question)
    q = _norm(question)
    plan = [{"tool": "web_search", "query": q, "reason": "pesquisa principal"}]
    if intent["current"]:
        plan.append({"tool": "web_search", "query": q + " fontes oficiais notícias recentes", "reason": "validar atualidade"})
    # Sempre busca preços quando parece compra/comparação de produto
    if intent["shopping"] or intent["compare"]:
        plan.append({"tool": "price_search", "query": q, "reason": "verificar preços reais"})
    if intent["compare"]:
        plan.append({"tool": "web_search", "query": q + " comparação especificações avaliações", "reason": "cruzar critérios"})
    return plan[:4]


def fetch_page(url: str, max_chars: int = 7000) -> Dict[str, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "text/html" not in ct and "text/plain" not in ct:
            return {"ok": False, "url": url, "error": "conteúdo não textual"}
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else url
        text = _clean_text(soup.get_text(" ", strip=True))
        return {"ok": True, "url": r.url, "title": title[:240], "text": text[:max_chars]}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def _keywords(question: str) -> List[str]:
    stop = {"para","como","qual","quais","onde","quando","porque","porquê","sobre","isso","esse","essa","com","uma","mais","menos","que","dos","das","de","do","da","e","ou","em","no","na","um","uma","eu","você","voce","me","the","and","for"}
    return [w for w in re.findall(r"[\wÀ-ÿ]+", question.lower()) if len(w) > 2 and w not in stop][:14]


def rank_evidence(question: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    kws = _keywords(question)
    ranked=[]
    for item in items:
        text = (item.get("title","") + " " + item.get("snippet","") + " " + item.get("text","")).lower()
        score = sum(text.count(k) for k in kws)
        if item.get("source"): score += 1
        item = dict(item); item["evidence_score"] = score
        ranked.append(item)
    return sorted(ranked, key=lambda x: x.get("evidence_score",0), reverse=True)


def synthesize_agent(question: str, sources: List[Dict[str, Any]], prices: List[Dict[str, Any]], intent: Dict[str, Any]) -> Dict[str, Any]:
    ranked = rank_evidence(question, sources)
    unique=[]; seen=set()
    for x in ranked:
        u=x.get("url","")
        if u and u not in seen:
            seen.add(u); unique.append(x)
    unique=unique[:6]
    parts=[]
    if prices:
        ordered=sorted(prices, key=lambda x: x.get("price", float("inf")))
        best=ordered[0]
        parts.append(f"Encontrei {len(prices)} ofertas reais. A menor encontrada é {best.get('priceText','—')} em {best.get('store','—')}.")
        if intent.get("compare") and len(ordered)>1:
            parts.append("Outras opções: " + "; ".join(f"{x.get('store','—')} — {x.get('priceText','—')}" for x in ordered[1:4]) + ".")
    if unique:
        # Síntese factual conservadora baseada nos snippets/textos extraídos.
        snippets=[]
        for x in unique[:4]:
            title = _norm(x.get("title") or "Fonte")
            # Evita títulos de tracking do Bing/Yahoo
            if "bing.com/ck" in title or title.startswith("http") and len(title) > 80:
                host = _domain_store(x.get("url") or "") or "Fonte"
                title = host
            snippet=_norm(x.get("snippet") or x.get("text") or "")
            if snippet: snippets.append(f"{title}: {snippet[:420]}")
        if snippets:
            parts.append("Principais evidências encontradas: " + " | ".join(snippets))
    if not parts:
        parts.append("Não consegui obter evidências suficientes para responder com segurança. Posso tentar outra estratégia de pesquisa.")
    return {"answer": "<br><br>".join(parts), "sources": unique, "confidence": "alta" if len(unique)>=3 else ("média" if unique else "baixa")}


def run_agent(question: str) -> Dict[str, Any]:
    question = _norm(question)
    if not question:
        return {"ok": False, "answer": "Faça uma pergunta para eu começar.", "plan": [], "sources": []}
    intent=infer_intent(question)
    plan=build_plan(question)
    web_queries=[x["query"] for x in plan if x["tool"]=="web_search"]
    price_queries=[x["query"] for x in plan if x["tool"]=="price_search"]
    all_web=[]; errors=[]
    # Executa sub-tarefas em paralelo para reduzir latência.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs=[ex.submit(web_search,q,8) for q in web_queries]
        for f in as_completed(futs):
            try:
                d=f.result(); all_web.extend(d.get("results",[])); errors.extend(d.get("errors",[]))
            except Exception as e: errors.append(str(e))
    # Remove duplicados e visita algumas fontes para ampliar o contexto.
    dedup=[]; seen=set()
    for x in all_web:
        u=x.get("url","").split("#",1)[0]
        if u and u not in seen:
            seen.add(u); dedup.append(x)
    pages=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs=[ex.submit(fetch_page,x["url"]) for x in dedup[:6] if x.get("url")]
        for f in as_completed(futs):
            try:
                d=f.result()
                if d.get("ok"): pages.append(d)
            except Exception as e: errors.append(str(e))
    sources=[]
    for x in dedup[:8]:
        merged=dict(x)
        match=next((p for p in pages if p.get("url")==x.get("url")),None)
        if match: merged.update(match)
        sources.append(merged)
    def _product_query(q: str) -> str:
        """Extrai um termo de produto mais limpo a partir da pergunta completa."""
        stop = {
            "qual", "quais", "melhor", "melhores", "mais", "menos", "barato", "barata",
            "bom", "boa", "para", "com", "sem", "que", "voce", "você", "recomenda",
            "comprar", "compra", "preco", "preço", "hoje", "agora", "vale", "pena",
            "custo", "beneficio", "benefício", "comparar", "comparacao", "comparação",
            "entre", "versus", "vs", "onde", "quanto", "custa", "escolher", "opcao",
            "opção", "opcoes", "opções", "sobre", "desse", "dessa", "deste", "desta",
        }
        tokens = [t for t in re.findall(r"[\wÀ-ÿ0-9]+", q.lower()) if t not in stop and len(t) > 1]
        return " ".join(tokens[:6]) if tokens else q

    prices=[]
    for q in price_queries[:1]:
        try:
            pq = _product_query(q)
            r=search_multifonte(pq or q, max_results=24)
            if isinstance(r, dict):
                errors.extend(r.get("errors") or [])
                r=r.get("results", [])
            if isinstance(r,list): prices=r
        except Exception as e: errors.append(str(e))
    result=synthesize_agent(question,sources,prices,intent)
    return {"ok": bool(sources or prices), "question":question, "intent":intent, "plan":plan, "sources":result["sources"], "prices":prices[:8], "answer":result["answer"], "confidence":result["confidence"], "errors":errors[:6]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[Economiza AI] {args[0]}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if path == "/api/learn":
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
                self._json(200, learn(payload))
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # API de busca
        if path == "/api/search":
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                self._json(400, {"error": "Parâmetro q obrigatório", "results": []})
                return
            print(f"Buscando: {q}")
            data = search_multifonte(q, max_results=24)
            results = data.get("results", [])
            best = escolher_melhor(results)
            self._json(200, {
                "query": q,
                "count": len(results),
                "results": results,
                "best": best,
                "source": "multifonte",
                "searchCoverage": "Mercado Livre API + Google + Bing + DuckDuckGo + Yahoo + Zoom + lojas",
                "stores": data.get("stores", []),
                "errors": data.get("errors", []),
            })
            return

        # Pesquisa ampla na web
        if path == "/api/web-search":
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                self._json(400, {"ok": False, "error": "Parâmetro q obrigatório", "results": []})
                return
            print(f"Pesquisa web: {q}")
            data = web_search(q, max_results=12)
            if not data["ok"]:
                self._json(502, data)
                return
            self._json(200, data)
            return

        # Agente autônomo: planeja, pesquisa, cruza fontes, consulta preços e sintetiza.
        if path == "/api/agent":
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                self._json(400, {"ok": False, "error": "Parâmetro q obrigatório"})
                return
            print(f"Agente: {q}")
            data = run_agent(q)
            if data.get("ok"):
                update_metric("agent_success")
            else:
                update_metric("agent_errors")
            self._json(200 if data.get("ok") else 502, data)
            return

        # Autoaprendizado controlado
        if path == "/api/learn":
            try:
                payload = json.loads((qs.get("data") or ["{}"])[0])
            except Exception:
                payload = {"kind": "feedback", "text": (qs.get("text") or [""])[0]}
            self._json(200, learn(payload))
            return

        if path == "/api/self-improve":
            action = (qs.get("action") or ["status"])[0]
            if action == "checkpoint":
                self._json(200, snapshot("auto-checkpoint"))
            elif action == "propose":
                self._json(200, propose_improvements())
            else:
                self._json(200, get_status())
            return

        if path == "/api/health":
            self._json(200, {"ok": True, "service": "Economiza AI", "port": PORT, "web_search": True, "price_search": True, "image_ocr": False})
            return

        # Arquivos estáticos
        if path == "/" or path == "":
            path = "/economiza_ai_painel.html"

        file_path = (DIR / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(DIR)) or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript",
            ".css": "text/css",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".py": "text/plain",
        }
        ctype = content_types.get(file_path.suffix.lower(), "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


def main():
    # Injeta no HTML a URL da API real (mesma origem)
    html_path = DIR / "economiza_ai_painel.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        if "USE_REAL_API" not in html:
            # marca será usada no JS
            pass

    global PORT
    requested = PORT
    last_error = None
    for candidate in range(requested, requested + 20):
        try:
            server = HTTPServer(("0.0.0.0", candidate), Handler)
            PORT = candidate
            break
        except OSError as exc:
            last_error = exc
    else:
        raise RuntimeError(f"Não foi possível abrir uma porta entre {requested} e {requested + 19}: {last_error}")

    print("=" * 50)
    print("  Economiza AI — Servidor de preços REAIS")
    print("=" * 50)
    print(f"  Abra no navegador:")
    print(f"  → http://127.0.0.1:{PORT}/")
    print()
    print("  API: http://127.0.0.1:{}/api/search?q=produto".format(PORT))
    print("  Ctrl+C para parar")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
        server.server_close()


if __name__ == "__main__":
    main()
