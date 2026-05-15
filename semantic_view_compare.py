import streamlit as st
import yaml
import re
import json
import copy
from snowflake.snowpark.context import get_active_session


session = get_active_session()


# ============================================================
# SHARED HELPERS
# ============================================================

def get_semantic_views_list():
    try:
        df = session.sql("SHOW SEMANTIC VIEWS IN ACCOUNT").collect()
        views = []
        for row in df:
            db = row["database_name"]
            schema = row["schema_name"]
            name = row["name"]
            views.append(f"{db}.{schema}.{name}")
        return views
    except Exception as e:
        st.error(f"Error listing semantic views: {e}")
        return []


def get_status_color(status: str) -> str:
    if status == "identical":
        return "#d4edda"
    elif status == "different":
        return "#f8d7da"
    return "#fff3cd"


def escape_html(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ============================================================
# SQL-SPECIFIC: Data classes, DDL parsing, SQL generation
# ============================================================

class ParsedObject:
    def __init__(self, name: str, raw_text: str, obj_type: str):
        self.name = name
        self.raw_text = raw_text
        self.obj_type = obj_type


class ParsedView:
    def __init__(self):
        self.full_ddl = ""
        self.view_name = ""
        self.comment = ""
        self.ai_sql_generation = ""
        self.ai_question_categorization = ""
        self.tables = []
        self.relationships = []
        self.facts = []
        self.dimensions = []
        self.metrics = []
        self.verified_queries = []
        self.extension_data = {}


def get_semantic_view_ddl(view_name: str) -> str:
    try:
        result = session.sql(f"SELECT GET_DDL('SEMANTIC VIEW', '{view_name}')").collect()
        if result:
            return result[0][0]
        return ""
    except Exception as e:
        st.error(f"Error getting DDL for {view_name}: {e}")
        return ""


def extract_block(ddl: str, keyword: str) -> str:
    pattern = rf'(?<!\w){keyword}\s*\('
    match = re.search(pattern, ddl, re.IGNORECASE)
    if not match:
        return ""
    start = match.end() - 1
    depth = 0
    i = start
    while i < len(ddl):
        if ddl[i] == '(':
            depth += 1
        elif ddl[i] == ')':
            depth -= 1
            if depth == 0:
                return ddl[start + 1:i].strip()
        i += 1
    return ""


def extract_single_quoted_value(ddl: str, keyword: str) -> str:
    pattern = rf"(?<!\w){keyword}\s+'"
    match = re.search(pattern, ddl, re.IGNORECASE)
    if not match:
        return ""
    i = match.end()
    result_chars = []
    while i < len(ddl):
        if ddl[i] == "'":
            if i + 1 < len(ddl) and ddl[i + 1] == "'":
                result_chars.append("'")
                i += 2
            else:
                break
        else:
            result_chars.append(ddl[i])
            i += 1
    return "".join(result_chars)


def parse_tables_block(block: str) -> list:
    if not block:
        return []
    tables = []
    entries = re.split(r',\s*\n\s*(?=[A-Z0-9_]+\.[A-Z0-9_]+\.[A-Z0-9_]+)', block, flags=re.IGNORECASE)
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        name_match = re.match(r'([A-Z0-9_]+\.[A-Z0-9_]+\.[A-Z0-9_]+)', entry, re.IGNORECASE)
        if name_match:
            tables.append(ParsedObject(name=name_match.group(1), raw_text=entry, obj_type="table"))
    return tables


def parse_simple_block(block: str, obj_type: str) -> list:
    if not block:
        return []
    objects = []
    entries = re.split(r',\s*\n', block)
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        name_match = re.match(r'([A-Z0-9_]+(?:\.[A-Z0-9_"]+)?)', entry, re.IGNORECASE)
        if name_match:
            objects.append(ParsedObject(name=name_match.group(1), raw_text=entry, obj_type=obj_type))
    return objects


def parse_metrics_block(block: str) -> list:
    if not block:
        return []
    metrics = []
    entries = re.split(r',\s*\n\s*(?=[A-Z0-9_]+\.[A-Z0-9_]+\s+as\s+)', block, flags=re.IGNORECASE)
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        name_match = re.match(r'([A-Z0-9_]+\.[A-Z0-9_]+)', entry, re.IGNORECASE)
        if name_match:
            metrics.append(ParsedObject(name=name_match.group(1), raw_text=entry, obj_type="metric"))
    return metrics


def parse_verified_queries_block(block: str) -> list:
    if not block:
        return []
    queries = []
    pattern = r'""([^""]+)""\s+AS\s*\('
    matches = list(re.finditer(pattern, block))
    if not matches:
        pattern = r'"([^"]+)"\s+AS\s*\('
        matches = list(re.finditer(pattern, block))
    for idx, match in enumerate(matches):
        question = match.group(1).strip()
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(block)
        raw_text = block[start:end].strip().rstrip(',').strip()
        queries.append(ParsedObject(name=question, raw_text=raw_text, obj_type="verified_query"))
    return queries


def parse_ddl(ddl: str) -> ParsedView:
    view = ParsedView()
    view.full_ddl = ddl

    name_match = re.search(r'create\s+or\s+replace\s+semantic\s+view\s+(\S+)', ddl, re.IGNORECASE)
    if name_match:
        view.view_name = name_match.group(1)

    comment_match = re.search(r"^\s+comment='", ddl, re.MULTILINE)
    if comment_match:
        view.comment = extract_single_quoted_value(ddl[comment_match.start():], "comment")

    view.ai_sql_generation = extract_single_quoted_value(ddl, "ai_sql_generation")
    view.ai_question_categorization = extract_single_quoted_value(ddl, "ai_question_categorization")

    view.tables = parse_tables_block(extract_block(ddl, "tables"))
    view.relationships = parse_simple_block(extract_block(ddl, "relationships"), "relationship")
    view.facts = parse_simple_block(extract_block(ddl, "facts"), "fact")
    view.dimensions = parse_simple_block(extract_block(ddl, "dimensions"), "dimension")
    view.metrics = parse_metrics_block(extract_block(ddl, "metrics"))
    view.verified_queries = parse_verified_queries_block(extract_block(ddl, "ai_verified_queries"))

    ext_match = re.search(r"with\s+extension\s*\(", ddl, re.IGNORECASE)
    if ext_match:
        ca_match = re.search(r"CA='(.*)'", ddl[ext_match.start():], re.DOTALL)
        if ca_match:
            ca_raw = ca_match.group(1)
            ca_json_str = ca_raw.replace('""', '"')
            try:
                view.extension_data = json.loads(ca_json_str)
            except json.JSONDecodeError:
                view.extension_data = {}

    return view


def normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip())


def compare_objects(left_objects: list, right_objects: list):
    left_map = {obj.name.upper(): obj for obj in left_objects}
    right_map = {obj.name.upper(): obj for obj in right_objects}
    all_names = list(dict.fromkeys(
        [obj.name.upper() for obj in left_objects] +
        [obj.name.upper() for obj in right_objects]
    ))
    results = []
    for name in all_names:
        left_obj = left_map.get(name)
        right_obj = right_map.get(name)
        if left_obj and right_obj:
            if normalize_text(left_obj.raw_text) == normalize_text(right_obj.raw_text):
                results.append((left_obj, right_obj, "identical"))
            else:
                results.append((left_obj, right_obj, "different"))
        elif left_obj:
            results.append((left_obj, None, "only_left"))
        else:
            results.append((None, right_obj, "only_right"))
    return results


def compare_single_values(left_val: str, right_val: str, name: str, obj_type: str):
    if not left_val and not right_val:
        return None
    left_obj = ParsedObject(name=name, raw_text=left_val, obj_type=obj_type) if left_val else None
    right_obj = ParsedObject(name=name, raw_text=right_val, obj_type=obj_type) if right_val else None
    if left_obj and right_obj:
        if normalize_text(left_val) == normalize_text(right_val):
            return (left_obj, right_obj, "identical")
        else:
            return (left_obj, right_obj, "different")
    elif left_obj:
        return (left_obj, None, "only_left")
    else:
        return (None, right_obj, "only_right")


def display_comparison_section_sql(comparisons, section_key_prefix, selections):
    for left_obj, right_obj, status in comparisons:
        name = (left_obj or right_obj).name
        key = f"{section_key_prefix}::{name}"
        color = get_status_color(status)

        col1, col2, col3 = st.columns([5, 5, 2])

        with col1:
            if left_obj:
                display_text = escape_html(left_obj.raw_text[:600])
                st.markdown(
                    f'<div style="background-color:{color};padding:8px;border-radius:4px;'
                    f'font-size:11px;white-space:pre-wrap;overflow-x:auto;margin-bottom:4px;">'
                    f'<code>{display_text}</code></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background-color:#e9ecef;padding:8px;border-radius:4px;'
                    'min-height:40px;color:#6c757d;font-style:italic;margin-bottom:4px;">'
                    '-- empty --</div>', unsafe_allow_html=True)

        with col2:
            if right_obj:
                display_text = escape_html(right_obj.raw_text[:600])
                st.markdown(
                    f'<div style="background-color:{color};padding:8px;border-radius:4px;'
                    f'font-size:11px;white-space:pre-wrap;overflow-x:auto;margin-bottom:4px;">'
                    f'<code>{display_text}</code></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background-color:#e9ecef;padding:8px;border-radius:4px;'
                    'min-height:40px;color:#6c757d;font-style:italic;margin-bottom:4px;">'
                    '-- empty --</div>', unsafe_allow_html=True)

        with col3:
            if status == "identical":
                st.markdown('<span style="color:green;">identical</span>', unsafe_allow_html=True)
                selections[key] = "left"
            elif status == "only_left":
                ch = st.selectbox("", ["Left (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "left" if "Left" in ch else "skip"
            elif status == "only_right":
                ch = st.selectbox("", ["Right (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "right" if "Right" in ch else "skip"
            else:
                ch = st.selectbox("", ["Left", "Right"], key=key, label_visibility="collapsed")
                selections[key] = "left" if ch == "Left" else "right"


def build_extension_json(selections: dict, left_view, right_view) -> str:
    def _table_lookup(ext_data):
        lookup = {}
        for t in ext_data.get("tables", []):
            lookup[t["name"].upper()] = t
        return lookup

    def _rel_lookup(ext_data):
        lookup = {}
        for r in ext_data.get("relationships", []):
            lookup[r["name"].upper()] = r
        return lookup

    left_ext_tables = _table_lookup(left_view.extension_data)
    right_ext_tables = _table_lookup(right_view.extension_data)
    left_ext_rels = _rel_lookup(left_view.extension_data)
    right_ext_rels = _rel_lookup(right_view.extension_data)

    selected_table_short_names = {}
    for key, choice in selections.items():
        if choice == "skip":
            continue
        parts = key.split("::", 1)
        if parts[0] == "table":
            full_name = parts[1] if len(parts) > 1 else ""
            short_name = full_name.split(".")[-1].upper()
            selected_table_short_names[short_name] = choice

    selected_dims_by_table = {}
    selected_facts_by_table = {}
    selected_metrics_by_table = {}

    for key, choice in selections.items():
        if choice == "skip":
            continue
        parts = key.split("::", 1)
        obj_type = parts[0]
        obj_name = parts[1] if len(parts) > 1 else ""

        if obj_type in ("dimension", "fact", "metric") and "." in obj_name:
            table_short = obj_name.split(".")[0].upper()
            col_name = obj_name.split(".", 1)[1].upper()
            if obj_type == "dimension":
                selected_dims_by_table.setdefault(table_short, set()).add(col_name)
            elif obj_type == "fact":
                selected_facts_by_table.setdefault(table_short, set()).add(col_name)
            elif obj_type == "metric":
                selected_metrics_by_table.setdefault(table_short, set()).add(col_name)

    selected_rel_names = {}
    for key, choice in selections.items():
        if choice == "skip":
            continue
        parts = key.split("::", 1)
        if parts[0] == "relationship":
            rel_name = parts[1].upper() if len(parts) > 1 else ""
            selected_rel_names[rel_name] = choice

    ext_tables = []
    for short_name, source in selected_table_short_names.items():
        ext_lookup = left_ext_tables if source == "left" else right_ext_tables
        src_table = ext_lookup.get(short_name, {})

        table_entry = {"name": src_table.get("name", short_name)}

        src_dims = {d["name"].upper(): d for d in src_table.get("dimensions", [])}
        src_time_dims = {d["name"].upper(): d for d in src_table.get("time_dimensions", [])}
        sel_dim_names = selected_dims_by_table.get(short_name, set())

        dims = []
        time_dims = []
        for dname in sel_dim_names:
            if dname in src_time_dims:
                time_dims.append(src_time_dims[dname])
            elif dname in src_dims:
                dims.append(src_dims[dname])
            else:
                dims.append({"name": dname})

        if dims:
            table_entry["dimensions"] = sorted(dims, key=lambda d: d["name"])
        if time_dims:
            table_entry["time_dimensions"] = sorted(time_dims, key=lambda d: d["name"])

        src_facts = {f["name"].upper(): f for f in src_table.get("facts", [])}
        sel_fact_names = selected_facts_by_table.get(short_name, set())
        facts = []
        for fname in sel_fact_names:
            if fname in src_facts:
                facts.append(src_facts[fname])
            else:
                facts.append({"name": fname})
        if facts:
            table_entry["facts"] = sorted(facts, key=lambda f: f["name"])

        src_metrics = {m["name"].upper(): m for m in src_table.get("metrics", [])}
        sel_metric_names = selected_metrics_by_table.get(short_name, set())
        metrics = []
        for mname in sel_metric_names:
            if mname in src_metrics:
                metrics.append(src_metrics[mname])
            else:
                metrics.append({"name": mname})
        if metrics:
            table_entry["metrics"] = sorted(metrics, key=lambda m: m["name"])

        src_fks = src_table.get("foreign_keys", [])
        fks = []
        for fk in src_fks:
            pkey_table_name = fk.get("pkey_table", {}).get("table", "").upper()
            if pkey_table_name in selected_table_short_names:
                fks.append(fk)
        if fks:
            table_entry["foreign_keys"] = fks

        src_filters = src_table.get("filters", [])
        if src_filters:
            table_entry["filters"] = src_filters

        ext_tables.append(table_entry)

    ext_rels = []
    for rel_name_upper, source in selected_rel_names.items():
        ext_lookup = left_ext_rels if source == "left" else right_ext_rels
        src_rel = ext_lookup.get(rel_name_upper)
        if src_rel:
            ext_rels.append(src_rel)
        else:
            ext_rels.append({"name": rel_name_upper})

    extension_obj = {}
    if ext_tables:
        extension_obj["tables"] = sorted(ext_tables, key=lambda t: t["name"])
    if ext_rels:
        extension_obj["relationships"] = sorted(ext_rels, key=lambda r: r["name"])

    if not extension_obj:
        return ""

    json_str = json.dumps(extension_obj, separators=(",", ":"), ensure_ascii=False)
    return f"with extension (CA='{json_str}')"


def generate_create_sql(view_name: str, selections: dict, left_view, right_view) -> str:
    lines = [f"CREATE OR REPLACE SEMANTIC VIEW {view_name}"]

    selected_tables = []
    selected_relationships = []
    selected_facts = []
    selected_dimensions = []
    selected_metrics = []
    selected_verified_queries = []
    selected_comment = ""
    selected_ai_sql_gen = ""
    selected_ai_question_cat = ""

    for key, choice in selections.items():
        if choice == "skip":
            continue
        parts = key.split("::", 1)
        obj_type = parts[0]
        obj_name = parts[1] if len(parts) > 1 else ""

        if obj_type == "comment":
            selected_comment = left_view.comment if choice == "left" else right_view.comment
        elif obj_type == "ai_sql_generation":
            selected_ai_sql_gen = left_view.ai_sql_generation if choice == "left" else right_view.ai_sql_generation
        elif obj_type == "ai_question_categorization":
            selected_ai_question_cat = (left_view.ai_question_categorization if choice == "left"
                                         else right_view.ai_question_categorization)
        elif obj_type == "table":
            source = left_view if choice == "left" else right_view
            obj = next((t for t in source.tables if t.name.upper() == obj_name.upper()), None)
            if obj:
                selected_tables.append(obj.raw_text)
        elif obj_type == "relationship":
            source = left_view if choice == "left" else right_view
            obj = next((r for r in source.relationships if r.name.upper() == obj_name.upper()), None)
            if obj:
                selected_relationships.append(obj.raw_text)
        elif obj_type == "fact":
            source = left_view if choice == "left" else right_view
            obj = next((f for f in source.facts if f.name.upper() == obj_name.upper()), None)
            if obj:
                selected_facts.append(obj.raw_text)
        elif obj_type == "dimension":
            source = left_view if choice == "left" else right_view
            obj = next((d for d in source.dimensions if d.name.upper() == obj_name.upper()), None)
            if obj:
                selected_dimensions.append(obj.raw_text)
        elif obj_type == "metric":
            source = left_view if choice == "left" else right_view
            obj = next((m for m in source.metrics if m.name.upper() == obj_name.upper()), None)
            if obj:
                selected_metrics.append(obj.raw_text)
        elif obj_type == "verified_query":
            source = left_view if choice == "left" else right_view
            obj = next((q for q in source.verified_queries if q.name == obj_name), None)
            if obj:
                selected_verified_queries.append(obj.raw_text)

    if selected_tables:
        lines.append("  TABLES (")
        lines.append(",\n".join(f"    {t}" for t in selected_tables))
        lines.append("  )")

    if selected_relationships:
        lines.append("  RELATIONSHIPS (")
        lines.append(",\n".join(f"    {r}" for r in selected_relationships))
        lines.append("  )")

    if selected_facts:
        lines.append("  FACTS (")
        lines.append(",\n".join(f"    {f}" for f in selected_facts))
        lines.append("  )")

    if selected_dimensions:
        lines.append("  DIMENSIONS (")
        lines.append(",\n".join(f"    {d}" for d in selected_dimensions))
        lines.append("  )")

    if selected_metrics:
        lines.append("  METRICS (")
        lines.append(",\n".join(f"    {m}" for m in selected_metrics))
        lines.append("  )")

    if selected_comment:
        escaped = selected_comment.replace("'", "''")
        lines.append(f"  COMMENT='{escaped}'")

    if selected_ai_sql_gen:
        escaped = selected_ai_sql_gen.replace("'", "''")
        lines.append(f"  AI_SQL_GENERATION '{escaped}'")

    if selected_ai_question_cat:
        escaped = selected_ai_question_cat.replace("'", "''")
        lines.append(f"  AI_QUESTION_CATEGORIZATION '{escaped}'")

    if selected_verified_queries:
        lines.append("  AI_VERIFIED_QUERIES (")
        lines.append(",\n".join(f"    {q}" for q in selected_verified_queries))
        lines.append("  )")

    extension_block = build_extension_json(selections, left_view, right_view)
    if extension_block:
        lines.append(f"  {extension_block}")

    lines.append(";")
    return "\n".join(lines)


# ============================================================
# YAML-SPECIFIC: Fetching, extraction, comparison, generation
# ============================================================

def get_semantic_view_yaml(view_name: str) -> dict:
    try:
        result = session.sql(
            f"SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('{view_name}')"
        ).collect()
        if result:
            yaml_str = result[0][0]
            return yaml.safe_load(yaml_str)
        return {}
    except Exception as e:
        st.error(f"Error fetching YAML for {view_name}: {e}")
        return {}


def extract_objects(view_yaml: dict) -> dict:
    objects = {
        "custom_instructions": [],
        "tables": [],
        "relationships": [],
        "verified_queries": [],
    }

    desc = view_yaml.get("description", "")
    if desc:
        objects["custom_instructions"].append({
            "name": "description",
            "content": desc
        })

    mci = view_yaml.get("module_custom_instructions", {})
    if mci.get("sql_generation"):
        objects["custom_instructions"].append({
            "name": "sql_generation",
            "content": mci["sql_generation"]
        })
    if mci.get("question_categorization"):
        objects["custom_instructions"].append({
            "name": "question_categorization",
            "content": mci["question_categorization"]
        })

    for table in view_yaml.get("tables", []):
        objects["tables"].append({
            "name": table.get("name", ""),
            "content": table
        })

    for rel in view_yaml.get("relationships", []):
        objects["relationships"].append({
            "name": rel.get("name", ""),
            "content": rel
        })

    for vq in view_yaml.get("verified_queries", []):
        q_name = vq.get("question", vq.get("name", ""))
        objects["verified_queries"].append({
            "name": q_name,
            "content": vq
        })

    return objects


def compare_category(left_objects: list, right_objects: list):
    left_map = {obj["name"].upper().strip(): obj for obj in left_objects}
    right_map = {obj["name"].upper().strip(): obj for obj in right_objects}

    all_names = list(dict.fromkeys(
        [obj["name"].upper().strip() for obj in left_objects] +
        [obj["name"].upper().strip() for obj in right_objects]
    ))

    results = []
    for name in all_names:
        left_obj = left_map.get(name)
        right_obj = right_map.get(name)
        if left_obj and right_obj:
            if left_obj["content"] == right_obj["content"]:
                results.append((left_obj, right_obj, "identical"))
            else:
                results.append((left_obj, right_obj, "different"))
        elif left_obj:
            results.append((left_obj, None, "only_left"))
        else:
            results.append((None, right_obj, "only_right"))
    return results


def format_yaml_snippet(content, max_lines=15) -> str:
    if isinstance(content, str):
        return content[:500]
    try:
        text = yaml.dump(content, default_flow_style=False, allow_unicode=True, sort_keys=False)
        lines = text.split('\n')
        if len(lines) > max_lines:
            text = '\n'.join(lines[:max_lines]) + '\n  ...'
        return text[:800]
    except Exception:
        return str(content)[:500]


def display_comparison_section_yaml(comparisons, section_key_prefix, selections):
    for left_obj, right_obj, status in comparisons:
        name = (left_obj or right_obj)["name"]
        key = f"{section_key_prefix}::{name}"
        color = get_status_color(status)

        col1, col2, col3 = st.columns([5, 5, 2])

        with col1:
            if left_obj:
                snippet = escape_html(format_yaml_snippet(left_obj["content"]))
                st.markdown(
                    f'<div style="background-color:{color};padding:8px;border-radius:4px;'
                    f'font-size:11px;white-space:pre-wrap;overflow-x:auto;margin-bottom:4px;">'
                    f'<code>{snippet}</code></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background-color:#e9ecef;padding:8px;border-radius:4px;'
                    'min-height:40px;color:#6c757d;font-style:italic;margin-bottom:4px;">'
                    '-- empty --</div>', unsafe_allow_html=True)

        with col2:
            if right_obj:
                snippet = escape_html(format_yaml_snippet(right_obj["content"]))
                st.markdown(
                    f'<div style="background-color:{color};padding:8px;border-radius:4px;'
                    f'font-size:11px;white-space:pre-wrap;overflow-x:auto;margin-bottom:4px;">'
                    f'<code>{snippet}</code></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background-color:#e9ecef;padding:8px;border-radius:4px;'
                    'min-height:40px;color:#6c757d;font-style:italic;margin-bottom:4px;">'
                    '-- empty --</div>', unsafe_allow_html=True)

        with col3:
            if status == "identical":
                st.markdown('<span style="color:green;">identical</span>', unsafe_allow_html=True)
                selections[key] = "left"
            elif status == "only_left":
                ch = st.selectbox("", ["Left (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "left" if "Left" in ch else "skip"
            elif status == "only_right":
                ch = st.selectbox("", ["Right (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "right" if "Right" in ch else "skip"
            else:
                ch = st.selectbox("", ["Left", "Right"], key=key, label_visibility="collapsed")
                selections[key] = "left" if ch == "Left" else "right"


def generate_merged_yaml(new_name: str, selections: dict,
                         left_objects: dict, right_objects: dict,
                         left_yaml: dict, right_yaml: dict) -> str:
    merged = {"name": new_name}

    left_ci_map = {obj["name"]: obj["content"] for obj in left_objects["custom_instructions"]}
    right_ci_map = {obj["name"]: obj["content"] for obj in right_objects["custom_instructions"]}
    left_table_map = {obj["name"].upper(): obj["content"] for obj in left_objects["tables"]}
    right_table_map = {obj["name"].upper(): obj["content"] for obj in right_objects["tables"]}
    left_rel_map = {obj["name"].upper(): obj["content"] for obj in left_objects["relationships"]}
    right_rel_map = {obj["name"].upper(): obj["content"] for obj in right_objects["relationships"]}
    left_vq_map = {obj["name"].upper().strip(): obj["content"] for obj in left_objects["verified_queries"]}
    right_vq_map = {obj["name"].upper().strip(): obj["content"] for obj in right_objects["verified_queries"]}

    description = ""
    sql_generation = ""
    question_categorization = ""
    tables = []
    relationships = []
    verified_queries = []

    for key, choice in selections.items():
        if choice == "skip":
            continue
        parts = key.split("::", 1)
        category = parts[0]
        obj_name = parts[1] if len(parts) > 1 else ""

        if category == "custom_instructions":
            source_map = left_ci_map if choice == "left" else right_ci_map
            value = source_map.get(obj_name, "")
            if obj_name == "description":
                description = value
            elif obj_name == "sql_generation":
                sql_generation = value
            elif obj_name == "question_categorization":
                question_categorization = value

        elif category == "table":
            source_map = left_table_map if choice == "left" else right_table_map
            content = source_map.get(obj_name.upper())
            if content:
                tables.append(copy.deepcopy(content))

        elif category == "relationship":
            source_map = left_rel_map if choice == "left" else right_rel_map
            content = source_map.get(obj_name.upper())
            if content:
                relationships.append(copy.deepcopy(content))

        elif category == "verified_query":
            source_map = left_vq_map if choice == "left" else right_vq_map
            content = source_map.get(obj_name.upper().strip())
            if content:
                verified_queries.append(copy.deepcopy(content))

    if description:
        merged["description"] = description
    if tables:
        merged["tables"] = tables
    if relationships:
        merged["relationships"] = relationships
    if sql_generation or question_categorization:
        mci = {}
        if sql_generation:
            mci["sql_generation"] = sql_generation
        if question_categorization:
            mci["question_categorization"] = question_categorization
        merged["module_custom_instructions"] = mci
    if verified_queries:
        merged["verified_queries"] = verified_queries

    return yaml.dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ============================================================
# SHARED UI: Legend + Header
# ============================================================

def render_legend():
    st.markdown("---")
    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        st.markdown('<div style="background-color:#d4edda;padding:6px 12px;border-radius:4px;text-align:center;">Identical</div>', unsafe_allow_html=True)
    with lc2:
        st.markdown('<div style="background-color:#f8d7da;padding:6px 12px;border-radius:4px;text-align:center;">Different</div>', unsafe_allow_html=True)
    with lc3:
        st.markdown('<div style="background-color:#fff3cd;padding:6px 12px;border-radius:4px;text-align:center;">Only in one</div>', unsafe_allow_html=True)


def render_header(left_label: str, right_label: str):
    st.markdown("---")
    hcol1, hcol2, hcol3 = st.columns([5, 5, 2])
    with hcol1:
        st.markdown(f"**LEFT: {left_label}**")
    with hcol2:
        st.markdown(f"**RIGHT: {right_label}**")
    with hcol3:
        st.markdown("**Choice**")


# ============================================================
# MAIN APP
# ============================================================

st.set_page_config(page_title="Semantic View Compare", layout="wide")
st.title("Semantic View Comparison")
st.markdown("Select two semantic views, compare differences, pick left/right for each object, then generate output.")

if "views_list" not in st.session_state:
    st.session_state.views_list = get_semantic_views_list()

views = st.session_state.views_list

if not views:
    st.warning("No semantic views found.")
    st.stop()

# --- View selection ---
col_left_sel, col_right_sel = st.columns(2)
with col_left_sel:
    left_choice = st.selectbox("Left semantic view", views, key="left_view_select")
with col_right_sel:
    right_choice = st.selectbox("Right semantic view", views, key="right_view_select",
                                 index=min(1, len(views) - 1))

# --- Single Compare button ---
if st.button("Compare", type="primary"):
    with st.spinner("Fetching data..."):
        left_ddl = get_semantic_view_ddl(left_choice)
        right_ddl = get_semantic_view_ddl(right_choice)
        left_yaml_data = get_semantic_view_yaml(left_choice)
        right_yaml_data = get_semantic_view_yaml(right_choice)
    if left_ddl and right_ddl:
        st.session_state.left_view = parse_ddl(left_ddl)
        st.session_state.right_view = parse_ddl(right_ddl)
        st.session_state.left_yaml = left_yaml_data
        st.session_state.right_yaml = right_yaml_data
        st.session_state.left_objects = extract_objects(left_yaml_data) if left_yaml_data else {}
        st.session_state.right_objects = extract_objects(right_yaml_data) if right_yaml_data else {}
        st.session_state.compared = True
        # Clear previous outputs
        st.session_state.pop("generated_sql", None)
        st.session_state.pop("generated_yaml", None)
    else:
        st.error("Failed to fetch data.")

# --- Shared comparison section (SQL-based granular view) ---
if st.session_state.get("compared"):
    left_view = st.session_state.left_view
    right_view = st.session_state.right_view
    selections = {}

    render_legend()
    render_header(left_view.view_name, right_view.view_name)

    # Custom Instructions
    st.markdown("### Custom Instructions")
    ci_items = []
    cmp = compare_single_values(left_view.comment, right_view.comment, "comment", "comment")
    if cmp:
        ci_items.append(cmp)
    cmp = compare_single_values(left_view.ai_sql_generation, right_view.ai_sql_generation, "ai_sql_generation", "ai_sql_generation")
    if cmp:
        ci_items.append(cmp)
    cmp = compare_single_values(left_view.ai_question_categorization, right_view.ai_question_categorization, "ai_question_categorization", "ai_question_categorization")
    if cmp:
        ci_items.append(cmp)

    for left_obj, right_obj, status in ci_items:
        name = (left_obj or right_obj).name
        obj_type_key = (left_obj or right_obj).obj_type
        key = f"{obj_type_key}::{name}"
        color = get_status_color(status)
        col1, col2, col3 = st.columns([5, 5, 2])
        with col1:
            if left_obj:
                st.markdown(f'<div style="background-color:{color};padding:8px;border-radius:4px;font-size:11px;white-space:pre-wrap;margin-bottom:4px;"><b>{name}</b><br><code>{escape_html(left_obj.raw_text[:400])}</code></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="background-color:#e9ecef;padding:8px;border-radius:4px;min-height:40px;color:#6c757d;font-style:italic;">-- empty --</div>', unsafe_allow_html=True)
        with col2:
            if right_obj:
                st.markdown(f'<div style="background-color:{color};padding:8px;border-radius:4px;font-size:11px;white-space:pre-wrap;margin-bottom:4px;"><b>{name}</b><br><code>{escape_html(right_obj.raw_text[:400])}</code></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="background-color:#e9ecef;padding:8px;border-radius:4px;min-height:40px;color:#6c757d;font-style:italic;">-- empty --</div>', unsafe_allow_html=True)
        with col3:
            if status == "identical":
                st.markdown('<span style="color:green;">identical</span>', unsafe_allow_html=True)
                selections[key] = "left"
            elif status == "only_left":
                ch = st.selectbox("", ["Left (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "left" if "Left" in ch else "skip"
            elif status == "only_right":
                ch = st.selectbox("", ["Right (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "right" if "Right" in ch else "skip"
            else:
                ch = st.selectbox("", ["Left", "Right"], key=key, label_visibility="collapsed")
                selections[key] = "left" if ch == "Left" else "right"

    # Tables
    if left_view.tables or right_view.tables:
        st.markdown("### Tables")
        display_comparison_section_sql(compare_objects(left_view.tables, right_view.tables), "table", selections)

    # Relationships
    if left_view.relationships or right_view.relationships:
        st.markdown("### Relationships")
        display_comparison_section_sql(compare_objects(left_view.relationships, right_view.relationships), "relationship", selections)

    # Facts
    if left_view.facts or right_view.facts:
        st.markdown("### Facts")
        display_comparison_section_sql(compare_objects(left_view.facts, right_view.facts), "fact", selections)

    # Dimensions
    if left_view.dimensions or right_view.dimensions:
        st.markdown("### Dimensions")
        display_comparison_section_sql(compare_objects(left_view.dimensions, right_view.dimensions), "dimension", selections)

    # Metrics
    if left_view.metrics or right_view.metrics:
        st.markdown("### Metrics")
        display_comparison_section_sql(compare_objects(left_view.metrics, right_view.metrics), "metric", selections)

    # Verified Queries
    if left_view.verified_queries or right_view.verified_queries:
        st.markdown("### Verified Queries")
        display_comparison_section_sql(compare_objects(left_view.verified_queries, right_view.verified_queries), "verified_query", selections)

    # --- Generate Output ---
    st.markdown("---")
    st.markdown("## Generate Output")

    col_schema, col_name = st.columns(2)
    with col_schema:
        target_schema = st.text_input("Target schema (DB.SCHEMA)",
                                       value="PKOWALSKI5.PUBLIC", key="target_schema")
    with col_name:
        new_view_name = st.text_input("New Semantic View name",
                                       value="MERGED_VIEW", key="new_view_name")

    if st.button("Generate Output", type="primary"):
        fqn = f"{target_schema}.{new_view_name}"

        # Generate SQL
        sql = generate_create_sql(fqn, selections, left_view, right_view)
        st.session_state.generated_sql = sql

        # Generate YAML -- remap SQL-based selection keys to YAML-compatible keys
        left_yaml = st.session_state.get("left_yaml", {})
        right_yaml = st.session_state.get("right_yaml", {})
        left_objects = st.session_state.get("left_objects", {})
        right_objects = st.session_state.get("right_objects", {})
        if left_yaml and right_yaml and left_objects and right_objects:
            yaml_selections = {}
            for k, v in selections.items():
                parts = k.split("::", 1)
                category = parts[0]
                obj_name = parts[1] if len(parts) > 1 else ""

                if category == "comment":
                    yaml_selections["custom_instructions::description"] = v
                elif category == "ai_sql_generation":
                    yaml_selections["custom_instructions::sql_generation"] = v
                elif category == "ai_question_categorization":
                    yaml_selections["custom_instructions::question_categorization"] = v
                elif category == "table":
                    # SQL uses FQN (DB.SCHEMA.TABLE), YAML uses short name
                    short_name = obj_name.split(".")[-1] if "." in obj_name else obj_name
                    yaml_selections[f"table::{short_name}"] = v
                elif category == "relationship":
                    yaml_selections[k] = v
                elif category == "verified_query":
                    yaml_selections[k] = v

            merged_yaml = generate_merged_yaml(
                new_view_name, yaml_selections,
                left_objects, right_objects,
                left_yaml, right_yaml
            )
            st.session_state.generated_yaml = merged_yaml

    # --- Output tabs ---
    if st.session_state.get("generated_sql") or st.session_state.get("generated_yaml"):
        tab_sql, tab_yaml = st.tabs(["SQL", "YAML"])

        with tab_sql:
            if st.session_state.get("generated_sql"):
                st.code(st.session_state.generated_sql, language="sql")
                if st.button("Execute in Snowflake", key="sql_exec_btn"):
                    try:
                        session.sql(st.session_state.generated_sql.rstrip(';')).collect()
                        st.success("Semantic View created successfully!")
                    except Exception as e:
                        st.error(f"Execution error: {e}")

        with tab_yaml:
            if st.session_state.get("generated_yaml"):
                st.code(st.session_state.generated_yaml, language="yaml")
