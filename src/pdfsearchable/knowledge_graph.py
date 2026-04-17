"""
Grafo de conhecimento entre documentos.

Constrói automaticamente uma rede de entidades (pessoas, organizações, e-mails,
CPFs, CNPJs, valores, locais) extraídas de todos os PDFs indexados e gera um
grafo interactivo D3.js para visualização.

Dois nós partilham uma aresta quando co-ocorrem no mesmo documento.
O peso da aresta = número de documentos onde ambos co-ocorrem.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_log = logging.getLogger("pdfsearchable.knowledge_graph")

# ---------------------------------------------------------------------------
# Tipos de nó
# ---------------------------------------------------------------------------

NODE_TYPE_DOCUMENT = "document"
NODE_TYPE_PERSON = "person"
NODE_TYPE_ORG = "org"
NODE_TYPE_EMAIL = "email"
NODE_TYPE_CPF = "cpf"
NODE_TYPE_CNPJ = "cnpj"
NODE_TYPE_MONETARY = "monetary"
NODE_TYPE_LOCATION = "location"
NODE_TYPE_DATE = "date"

# Cores por tipo de nó (CSS)
_NODE_COLORS: dict[str, str] = {
    NODE_TYPE_DOCUMENT: "#4A90D9",
    NODE_TYPE_PERSON: "#E67E22",
    NODE_TYPE_ORG: "#27AE60",
    NODE_TYPE_EMAIL: "#8E44AD",
    NODE_TYPE_CPF: "#E74C3C",
    NODE_TYPE_CNPJ: "#C0392B",
    NODE_TYPE_MONETARY: "#F39C12",
    NODE_TYPE_LOCATION: "#16A085",
    NODE_TYPE_DATE: "#7F8C8D",
}

# Raios dos nós por tipo (px)
_NODE_RADII: dict[str, int] = {
    NODE_TYPE_DOCUMENT: 18,
    NODE_TYPE_PERSON: 12,
    NODE_TYPE_ORG: 14,
    NODE_TYPE_EMAIL: 8,
    NODE_TYPE_CPF: 8,
    NODE_TYPE_CNPJ: 8,
    NODE_TYPE_MONETARY: 8,
    NODE_TYPE_LOCATION: 10,
    NODE_TYPE_DATE: 8,
}

# ---------------------------------------------------------------------------
# Construção do grafo
# ---------------------------------------------------------------------------


def build_graph(files: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Constrói o grafo de conhecimento a partir da lista de arquivos do índice.

    Retorna ``{"nodes": [...], "edges": [...]}`` onde:
    - nodes: ``{"id": str, "label": str, "type": str, "color": str, "radius": int,
               "doc_count": int, "doc_ids": [str]}``
    - edges: ``{"source": str, "target": str, "weight": int, "doc_ids": [str]}``
    """
    # node_id → {type, label, doc_ids}
    nodes: dict[str, dict[str, Any]] = {}
    # (node_id_a, node_id_b) → {weight, doc_ids}  — sempre a < b para evitar duplicatas
    edge_map: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"weight": 0, "doc_ids": []}
    )

    for f in files:
        file_id: str = f.get("id", "")
        name: str = f.get("name", file_id)
        doc_type: str = f.get("doc_type") or f.get("type") or "documento"
        meta: dict[str, Any] = f.get("metadata") or {}

        # Nó do documento
        doc_node_id = f"doc:{file_id}"
        nodes[doc_node_id] = {
            "id": doc_node_id,
            "label": name[:40],
            "type": NODE_TYPE_DOCUMENT,
            "doc_type": doc_type,
            "doc_ids": [file_id],
        }

        # Entidades deste documento
        doc_entities: list[str] = []

        def _add_entity(eid: str, label: str, ntype: str) -> None:
            """Regista entidade e liga ao documento."""
            if not eid or not label:
                return
            if eid not in nodes:
                nodes[eid] = {
                    "id": eid,
                    "label": label[:60],
                    "type": ntype,
                    "doc_ids": [],
                }
            if file_id not in nodes[eid]["doc_ids"]:
                nodes[eid]["doc_ids"].append(file_id)
            doc_entities.append(eid)
            # Aresta entidade ↔ documento
            key = tuple(sorted([doc_node_id, eid]))
            edge_map[key]["weight"] += 1  # type: ignore[index]
            if file_id not in edge_map[key]["doc_ids"]:  # type: ignore[index]
                edge_map[key]["doc_ids"].append(file_id)

        # E-mails
        for email in meta.get("identified_emails") or []:
            _add_entity(f"email:{email.lower()}", email.lower(), NODE_TYPE_EMAIL)

        # CPFs
        for cpf in meta.get("identified_cpfs") or []:
            clean = re.sub(r"\D", "", cpf)
            _add_entity(f"cpf:{clean}", cpf, NODE_TYPE_CPF)

        # CNPJs
        for cnpj in meta.get("identified_cnpjs") or []:
            clean = re.sub(r"\D", "", cnpj)
            _add_entity(f"cnpj:{clean}", cnpj, NODE_TYPE_CNPJ)

        # Partes / participantes
        for party in meta.get("parties") or []:
            if not party or len(party) < 3:
                continue
            ptype = _guess_party_type(party)
            _add_entity(f"party:{party.lower()[:80]}", party, ptype)

        # Valores monetários (apenas os maiores, para não poluir)
        amounts = meta.get("monetary_amounts") or []
        if amounts:
            for amt in amounts[:3]:
                _add_entity(f"money:{amt}", amt, NODE_TYPE_MONETARY)

        # Datas identificadas (apenas as únicas, primeiras 3)
        for date in (meta.get("identified_dates") or [])[:3]:
            _add_entity(f"date:{date}", date, NODE_TYPE_DATE)

        # Arestas entre entidades do mesmo documento (co-ocorrência)
        unique_entities = list(dict.fromkeys(doc_entities))  # preservar ordem, sem duplicatas
        for i, ea in enumerate(unique_entities):
            for eb in unique_entities[i + 1 :]:
                key = tuple(sorted([ea, eb]))
                edge_map[key]["weight"] += 1  # type: ignore[index]
                if file_id not in edge_map[key]["doc_ids"]:  # type: ignore[index]
                    edge_map[key]["doc_ids"].append(file_id)

    # Construir listas finais
    node_list = []
    for n in nodes.values():
        ntype = n["type"]
        node_list.append(
            {
                "id": n["id"],
                "label": n["label"],
                "type": ntype,
                "color": _NODE_COLORS.get(ntype, "#95A5A6"),
                "radius": _NODE_RADII.get(ntype, 8),
                "doc_count": len(n.get("doc_ids", [])),
                "doc_ids": n.get("doc_ids", []),
                "doc_type": n.get("doc_type", ""),
            }
        )

    edge_list = [
        {
            "source": k[0],
            "target": k[1],
            "weight": v["weight"],
            "doc_ids": v["doc_ids"],
        }
        for k, v in edge_map.items()
        if v["weight"] > 0
    ]

    return {"nodes": node_list, "edges": edge_list}


def _guess_party_type(name: str) -> str:
    """Heurística simples para distinguir pessoa física de organização."""
    org_keywords = (
        "ltda",
        "s.a.",
        "s/a",
        "sa ",
        "eireli",
        "epp",
        "me ",
        "inc.",
        "corp.",
        "empresa",
        "companhia",
        "associação",
        "fundação",
        "instituto",
        "banco",
        "município",
        "estado ",
        "união ",
        "federal",
        "ministério",
        "secretaria",
        "prefeitura",
        "câmara",
        "tribunal",
        "universidade",
        "faculdade",
    )
    lower = name.lower()
    if any(kw in lower for kw in org_keywords):
        return NODE_TYPE_ORG
    # Se tem mais de 3 palavras e nenhum indicador de org, provavelmente pessoa
    words = lower.split()
    if len(words) >= 2:
        return NODE_TYPE_PERSON
    return NODE_TYPE_ORG


# ---------------------------------------------------------------------------
# Geração de HTML
# ---------------------------------------------------------------------------

_GRAPH_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Grafo de Conhecimento — pdfsearchable</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #1a1a2e; color: #eee; height: 100vh; overflow: hidden; }
  #toolbar { position: fixed; top: 0; left: 0; right: 0; height: 52px; background: #16213e;
             border-bottom: 1px solid #0f3460; display: flex; align-items: center;
             padding: 0 16px; gap: 12px; z-index: 100; }
  #toolbar h1 { font-size: 15px; font-weight: 600; color: #e2e8f0; white-space: nowrap; }
  #search { flex: 1; max-width: 280px; background: #0f3460; border: 1px solid #1e4080;
            border-radius: 6px; color: #eee; padding: 6px 10px; font-size: 13px; }
  #search::placeholder { color: #6b7280; }
  .filter-btn { background: #0f3460; border: 1px solid #1e4080; color: #94a3b8;
                border-radius: 4px; padding: 4px 8px; font-size: 11px; cursor: pointer;
                transition: all 0.15s; }
  .filter-btn.active { background: #4A90D9; border-color: #4A90D9; color: #fff; }
  #stats { font-size: 11px; color: #6b7280; white-space: nowrap; margin-left: auto; }
  #graph { position: fixed; top: 52px; left: 0; right: 0; bottom: 0; }
  #tooltip { position: fixed; background: rgba(22,33,62,0.95); border: 1px solid #0f3460;
             border-radius: 8px; padding: 10px 14px; font-size: 12px; pointer-events: none;
             display: none; max-width: 260px; z-index: 200; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
  #tooltip .tt-title { font-weight: 600; margin-bottom: 4px; font-size: 13px; }
  #tooltip .tt-type { font-size: 10px; color: #94a3b8; text-transform: uppercase;
                      letter-spacing: 0.5px; margin-bottom: 6px; }
  #tooltip .tt-docs { font-size: 11px; color: #cbd5e1; }
  .node { cursor: pointer; }
  .node circle { stroke-width: 1.5px; transition: r 0.15s; }
  .node text { font-size: 9px; fill: #e2e8f0; pointer-events: none;
               text-shadow: 0 0 3px #000; }
  .link { stroke: #2d4a7a; stroke-opacity: 0.5; }
  .link.highlighted { stroke: #60a5fa; stroke-opacity: 0.9; }
  .node.dimmed circle { opacity: 0.15; }
  .node.dimmed text { opacity: 0.1; }
  .link.dimmed { opacity: 0.05; }
  #legend { position: fixed; bottom: 16px; left: 16px; background: rgba(22,33,62,0.9);
            border: 1px solid #0f3460; border-radius: 8px; padding: 10px 14px; font-size: 11px; }
  #legend h3 { font-size: 11px; color: #94a3b8; margin-bottom: 8px; }
  .leg-item { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .leg-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
</style>
</head>
<body>
<div id="toolbar">
  <h1>🕸 Grafo de Conhecimento</h1>
  <input id="search" type="text" placeholder="Pesquisar entidade…">
  <button class="filter-btn active" data-type="all">Todos</button>
  <button class="filter-btn" data-type="document">Documentos</button>
  <button class="filter-btn" data-type="person">Pessoas</button>
  <button class="filter-btn" data-type="org">Organizações</button>
  <button class="filter-btn" data-type="email">E-mails</button>
  <span id="stats"></span>
</div>
<svg id="graph"></svg>
<div id="tooltip">
  <div class="tt-title" id="tt-title"></div>
  <div class="tt-type" id="tt-type"></div>
  <div class="tt-docs" id="tt-docs"></div>
</div>
<div id="legend">
  <h3>Legenda</h3>
  <div class="leg-item"><div class="leg-dot" style="background:#4A90D9"></div>Documento</div>
  <div class="leg-item"><div class="leg-dot" style="background:#E67E22"></div>Pessoa</div>
  <div class="leg-item"><div class="leg-dot" style="background:#27AE60"></div>Organização</div>
  <div class="leg-item"><div class="leg-dot" style="background:#8E44AD"></div>E-mail</div>
  <div class="leg-item"><div class="leg-dot" style="background:#E74C3C"></div>CPF</div>
  <div class="leg-item"><div class="leg-dot" style="background:#C0392B"></div>CNPJ</div>
  <div class="leg-item"><div class="leg-dot" style="background:#F39C12"></div>Valor</div>
  <div class="leg-item"><div class="leg-dot" style="background:#16A085"></div>Local</div>
</div>
<script>
const GRAPH_DATA = {GRAPH_DATA_JSON};
</script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function() {
  const nodes = GRAPH_DATA.nodes.map(d => ({...d}));
  const links = GRAPH_DATA.edges.map(d => ({...d}));
  const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));

  const svg = d3.select("#graph");
  const W = () => window.innerWidth;
  const H = () => window.innerHeight - 52;

  const g = svg.append("g");

  svg.call(d3.zoom()
    .scaleExtent([0.1, 8])
    .on("zoom", e => g.attr("transform", e.transform))
  );

  let activeFilter = "all";

  function applyFilter() {
    const q = document.getElementById("search").value.toLowerCase();
    nodes.forEach(n => {
      n.visible = (activeFilter === "all" || n.type === activeFilter) &&
                  (!q || n.label.toLowerCase().includes(q));
    });
    update();
  }

  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.dataset.type;
      applyFilter();
    });
  });
  document.getElementById("search").addEventListener("input", applyFilter);

  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id).distance(d => 60 + d.weight * 10))
    .force("charge", d3.forceManyBody().strength(-120))
    .force("center", d3.forceCenter(W() / 2, H() / 2))
    .force("collision", d3.forceCollide().radius(d => d.radius + 4));

  const link = g.append("g").selectAll("line")
    .data(links).join("line")
    .attr("class", "link")
    .attr("stroke-width", d => Math.min(1 + d.weight * 0.5, 4));

  const node = g.append("g").selectAll("g")
    .data(nodes).join("g")
    .attr("class", "node")
    .call(d3.drag()
      .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  node.append("circle")
    .attr("r", d => d.radius)
    .attr("fill", d => d.color)
    .attr("stroke", d => d3.color(d.color).brighter(0.5));

  node.append("text")
    .attr("dy", d => d.radius + 10)
    .attr("text-anchor", "middle")
    .text(d => d.label.length > 20 ? d.label.slice(0, 18) + "…" : d.label);

  const tt = document.getElementById("tooltip");
  const ttTitle = document.getElementById("tt-title");
  const ttType = document.getElementById("tt-type");
  const ttDocs = document.getElementById("tt-docs");

  const typeLabel = {
    document:"Documento", person:"Pessoa", org:"Organização", email:"E-mail",
    cpf:"CPF", cnpj:"CNPJ", monetary:"Valor", location:"Local", date:"Data"
  };

  node.on("mouseover", (e, d) => {
    tt.style.display = "block";
    ttTitle.textContent = d.label;
    ttType.textContent = typeLabel[d.type] || d.type;
    ttDocs.textContent = `Em ${d.doc_count} documento(s)`;
    // Highlight neighbours
    const connected = new Set([d.id]);
    links.forEach(l => {
      const src = typeof l.source === "object" ? l.source.id : l.source;
      const tgt = typeof l.target === "object" ? l.target.id : l.target;
      if (src === d.id) connected.add(tgt);
      if (tgt === d.id) connected.add(src);
    });
    node.classed("dimmed", n => !connected.has(n.id));
    link.classed("dimmed", l => {
      const src = typeof l.source === "object" ? l.source.id : l.source;
      const tgt = typeof l.target === "object" ? l.target.id : l.target;
      return src !== d.id && tgt !== d.id;
    });
    link.classed("highlighted", l => {
      const src = typeof l.source === "object" ? l.source.id : l.source;
      const tgt = typeof l.target === "object" ? l.target.id : l.target;
      return src === d.id || tgt === d.id;
    });
  })
  .on("mousemove", e => { tt.style.left = (e.clientX + 12) + "px"; tt.style.top = (e.clientY - 10) + "px"; })
  .on("mouseout", () => {
    tt.style.display = "none";
    node.classed("dimmed", false);
    link.classed("dimmed", false).classed("highlighted", false);
  });

  sim.on("tick", () => {
    link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });

  document.getElementById("stats").textContent =
    `${nodes.length} nós · ${links.length} ligações`;

  window.addEventListener("resize", () => {
    sim.force("center", d3.forceCenter(W()/2, H()/2)).alpha(0.3).restart();
  });

  function update() {
    node.style("display", d => d.visible === false ? "none" : null);
  }
  nodes.forEach(n => n.visible = true);
  update();
})();
</script>
</body>
</html>
"""


def generate_graph_html(files: list[dict[str, Any]], output_path: Path) -> Path:
    """
    Gera o arquivo HTML do grafo interactivo D3.js.

    Args:
        files: Lista de arquivos do índice (``load_index()["files"]``).
        output_path: Caminho onde gravar o HTML.

    Returns:
        O caminho do arquivo gerado.
    """
    graph_data = build_graph(files)
    graph_json = json.dumps(graph_data, ensure_ascii=False)
    html = _GRAPH_HTML_TEMPLATE.replace("{GRAPH_DATA_JSON}", graph_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp.html")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(output_path)
    _log.info(
        "Grafo gerado: %s (%d nós, %d arestas)",
        output_path,
        len(graph_data["nodes"]),
        len(graph_data["edges"]),
    )
    return output_path


def get_graph_stats(files: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Retorna estatísticas do grafo sem gerar HTML.

    Returns:
        Dict com ``nodes``, ``edges``, ``entity_types`` (contagem por tipo).
    """
    graph = build_graph(files)
    type_counts: dict[str, int] = defaultdict(int)
    for n in graph["nodes"]:
        type_counts[n["type"]] += 1
    return {
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "entity_types": dict(type_counts),
    }
