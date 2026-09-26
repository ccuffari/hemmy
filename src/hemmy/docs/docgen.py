"""Documentazione vivente dell'infrastruttura.

Mantiene uno STATO STRUTTURATO (JSON) che riflette lo stato ATTUALE delle risorse
gestite dall'agente (non lo storico) e rigenera un markdown con diagramma Mermaid.

È aggiornato in modo deterministico: a ogni azione [WRITE] riuscita, `apply(...)`
modifica lo stato (aggiunta/rinomina/cambio puntamento/rimozione). Al termine del
turno l'agente chiama `save()` che riscrive stato + markdown.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any


def _account_from_conn(conn: str | None) -> str | None:
    if not conn:
        return None
    m = re.search(r"AccountName=([^;]+)", conn, re.IGNORECASE)
    return m.group(1) if m else None


def _empty_state() -> dict[str, Any]:
    return {
        "resource_groups": {},          # name -> {location}
        "storage_accounts": {},         # name -> {resource_group, location, sku}
        "storage": {},                  # account -> {containers, tables, queues, file_shares}
        "sql_servers": {},              # name -> {resource_group, location, databases:[]}
        "sql_tables": {},               # db_label -> [schema.table]
        "adf": {"factory": None, "linked_services": {}, "datasets": {}, "pipelines": {}},
        "networking": {"vnets": {}, "private_endpoints": [], "nsgs": []},
        "notes": {},                    # titolo -> contenuto markdown (custom, persistente)
    }


class DocStore:
    def __init__(
        self,
        state_path: str,
        markdown_path: str,
        default_account: str | None = None,
        factory_name: str | None = None,
    ) -> None:
        self.state_path = state_path
        self.markdown_path = markdown_path
        self.default_account = default_account or _account_from_conn(
            os.environ.get("BLOB_CONNECTION_STRING")
        )
        self.state = _empty_state()
        self.state["adf"]["factory"] = factory_name or os.environ.get("ADF_FACTORY_NAME")
        self._load()

    # ------------------------------------------------------------- persistence

    def _load(self) -> None:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                merged = _empty_state()
                merged.update(loaded)
                merged["adf"] = {**_empty_state()["adf"], **loaded.get("adf", {})}
                self.state = merged
            except (json.JSONDecodeError, OSError):
                pass

    def reset_state(self) -> None:
        """Svuota l'inventario (mantiene factory e le note custom)."""
        factory = self.state["adf"].get("factory")
        notes = self.state.get("notes", {})
        self.state = _empty_state()
        self.state["adf"]["factory"] = factory
        self.state["notes"] = notes

    def add_note(self, title: str, content: str) -> None:
        """Aggiunge/aggiorna una nota custom (upsert per titolo)."""
        self.state.setdefault("notes", {})[title] = content

    def remove_note(self, title: str) -> bool:
        """Rimuove una nota custom. True se esisteva."""
        return self.state.get("notes", {}).pop(title, None) is not None

    def save(self) -> None:
        directory = os.path.dirname(self.state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        with open(self.markdown_path, "w", encoding="utf-8") as f:
            f.write(self.render_markdown())

    # --------------------------------------------------------- storage helpers

    def _account(self, name: str | None) -> dict[str, Any]:
        name = name or self.default_account or "default"
        return self.state["storage"].setdefault(
            name, {"containers": [], "tables": [], "queues": [], "file_shares": []}
        )

    @staticmethod
    def _add(lst: list[str], item: str) -> None:
        if item not in lst:
            lst.append(item)

    @staticmethod
    def _remove(lst: list[str], item: str) -> None:
        if item in lst:
            lst.remove(item)

    # ------------------------------------------------------------------- apply

    def apply(self, action: str, args: dict[str, Any], result: Any = None) -> bool:
        """Aggiorna lo stato in base a un'azione di scrittura riuscita. True se cambia."""
        args = args or {}
        result = result if isinstance(result, dict) else {}
        try:
            handler = getattr(self, "_h_" + action.replace(".", "_"), None)
            if handler is None:
                return False
            handler(args, result)
            return True
        except Exception:  # noqa: BLE001 - la doc non deve mai rompere l'azione
            return False

    # --- resource / management plane ---
    def _h_resource_create_resource_group(self, args, result):
        self.state["resource_groups"][args["name"]] = {"location": args.get("location")}

    def _h_blob_create_storage_account(self, args, result):
        self.state["storage_accounts"][args["account_name"]] = {
            "resource_group": args.get("resource_group"),
            "location": args.get("location"),
            "sku": args.get("sku", "Standard_LRS"),
        }
        self._account(args["account_name"])

    def _h_sql_create_sql_server(self, args, result):
        self.state["sql_servers"][args["server_name"]] = {
            "resource_group": args.get("resource_group"),
            "location": args.get("location"),
            "databases": [],
        }

    def _h_sql_create_sql_database(self, args, result):
        srv = self.state["sql_servers"].setdefault(
            args["server_name"], {"databases": []}
        )
        self._add(srv["databases"], args["database_name"])

    # --- storage data plane (default account) ---
    def _h_blob_create_container(self, args, result):
        self._add(self._account(None)["containers"], args["container"])

    def _h_blob_delete_container(self, args, result):
        self._remove(self._account(None)["containers"], args["container"])

    def _h_table_create_table(self, args, result):
        self._add(self._account(None)["tables"], args["table_name"])

    def _h_table_delete_table(self, args, result):
        self._remove(self._account(None)["tables"], args["table_name"])

    def _h_queue_create_queue(self, args, result):
        self._add(self._account(None)["queues"], args["queue_name"])

    def _h_queue_delete_queue(self, args, result):
        self._remove(self._account(None)["queues"], args["queue_name"])

    def _h_fileshare_create_share(self, args, result):
        self._add(self._account(None)["file_shares"], args["share_name"])

    def _h_fileshare_delete_share(self, args, result):
        self._remove(self._account(None)["file_shares"], args["share_name"])

    # --- ADF ---
    def _h_adf_create_linked_service(self, args, result):
        self.state["adf"]["linked_services"][args["name"]] = {
            "type": args.get("service_type", "unknown")
        }

    def _h_adf_create_keyvault_linked_service(self, args, result):
        self.state["adf"]["linked_services"][args["name"]] = {
            "type": "AzureKeyVault",
            "vault_url": args.get("vault_url"),
        }

    def _h_adf_create_linked_service_kv(self, args, result):
        self.state["adf"]["linked_services"][args["name"]] = {
            "type": args.get("service_type", "unknown"),
            "secret_source": f"KV:{args.get('kv_linked_service')}/{args.get('secret_name')}",
        }

    def _h_adf_create_dataset(self, args, result):
        definition = args.get("definition", {})
        props = definition.get("properties", {})
        tp = props.get("typeProperties", {})
        target = tp.get("table") or tp.get("fileName") or (tp.get("location") or {}).get("fileName")
        self.state["adf"]["datasets"][args["name"]] = {
            "type": props.get("type", "unknown"),
            "linked_service": (props.get("linkedServiceName") or {}).get("referenceName"),
            "target": target,
        }

    def _h_adf_create_pipeline(self, args, result):
        definition = args.get("definition", {})
        activities = (definition.get("properties") or {}).get("activities", [])
        acts = []
        for a in activities:
            acts.append(
                {
                    "name": a.get("name"),
                    "type": a.get("type"),
                    "inputs": [r.get("referenceName") for r in a.get("inputs", [])],
                    "outputs": [r.get("referenceName") for r in a.get("outputs", [])],
                }
            )
        self.state["adf"]["pipelines"][args["pipeline_name"]] = {"activities": acts}

    def _h_adf_create_copy_pipeline(self, args, result):
        src_ds = result.get("source_dataset") or f"DS_src_{args['pipeline_name']}"
        sink_ds = result.get("sink_dataset") or f"DS_sink_{args['pipeline_name']}"
        fmt = args.get("source_format", "DelimitedText")
        self.state["adf"]["datasets"][src_ds] = {
            "type": fmt,
            "linked_service": args.get("source_linked_service"),
            "target": f"{args.get('source_container')}/{args.get('source_file')}",
        }
        self.state["adf"]["datasets"][sink_ds] = {
            "type": "AzureSqlTable",
            "linked_service": args.get("sink_linked_service"),
            "target": f"{args.get('sink_schema', 'dbo')}.{args.get('sink_table')}",
        }
        self.state["adf"]["pipelines"][args["pipeline_name"]] = {
            "activities": [
                {"name": "CopyBlobToSql", "type": "Copy", "inputs": [src_ds], "outputs": [sink_ds]}
            ]
        }

    def _h_adf_delete_linked_service(self, args, result):
        self.state["adf"]["linked_services"].pop(args["name"], None)

    def _h_adf_delete_dataset(self, args, result):
        self.state["adf"]["datasets"].pop(args["name"], None)

    def _h_adf_delete_pipeline(self, args, result):
        self.state["adf"]["pipelines"].pop(args["pipeline_name"], None)

    # --- SQL DDL (best-effort) ---
    def _h_sql_execute_write(self, args, result):
        self._apply_sql_ddl(args.get("query", ""), "default")

    def _h_sql_execute_write_for_connection(self, args, result):
        self._apply_sql_ddl(args.get("query", ""), args.get("resource_label", "default"))

    def _apply_sql_ddl(self, query: str, db: str) -> None:
        tables = self.state["sql_tables"].setdefault(db, [])
        for schema, name in re.findall(
            r"create\s+table\s+(?:\[?(\w+)\]?\.)?\[?(\w+)\]?", query, re.IGNORECASE
        ):
            self._add(tables, f"{schema or 'dbo'}.{name}")
        for schema, name in re.findall(
            r"drop\s+table\s+(?:\[?(\w+)\]?\.)?\[?(\w+)\]?", query, re.IGNORECASE
        ):
            self._remove(tables, f"{schema or 'dbo'}.{name}")
        # sp_rename di tabella: 'dbo.old' -> 'new'
        m = re.search(
            r"sp_rename\s+'(?:\[?\w+\]?\.)?\[?(\w+)\]?'\s*,\s*'\[?(\w+)\]?'\s*(?:;|$)",
            query,
            re.IGNORECASE,
        )
        if m and "COLUMN" not in query.upper():
            old, new = f"dbo.{m.group(1)}", f"dbo.{m.group(2)}"
            if old in tables:
                self._remove(tables, old)
                self._add(tables, new)

    # ----------------------------------------------------------------- render

    @staticmethod
    def _nid(prefix: str, name: str) -> str:
        return prefix + "_" + re.sub(r"[^0-9a-zA-Z]", "_", name or "")

    def render_markdown(self) -> str:
        s = self.state
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out: list[str] = [
            "# Infrastruttura Azure — stato attuale",
            "",
            f"_Ultimo aggiornamento: {ts}. Documento generato automaticamente: "
            "riflette lo stato corrente, non lo storico._",
            "",
        ]

        # Resource groups
        if s["resource_groups"]:
            out += ["## Resource Group", "", "| Nome | Location |", "|---|---|"]
            for name, d in s["resource_groups"].items():
                out.append(f"| {name} | {d.get('location','')} |")
            out.append("")

        # Storage
        if s["storage_accounts"] or s["storage"]:
            out += ["## Storage", ""]
            for name, d in s["storage_accounts"].items():
                out.append(
                    f"- **{name}** (rg: {d.get('resource_group','?')}, "
                    f"{d.get('location','?')}, {d.get('sku','?')})"
                )
            for acc, d in s["storage"].items():
                out.append(f"- Account `{acc}`:")
                for kind in ("containers", "tables", "queues", "file_shares"):
                    if d.get(kind):
                        out.append(f"  - {kind}: {', '.join(d[kind])}")
            out.append("")

        # SQL
        if s["sql_servers"] or s["sql_tables"]:
            out += ["## SQL", ""]
            for name, d in s["sql_servers"].items():
                dbs = ", ".join(d.get("databases", [])) or "—"
                out.append(f"- **{name}** (rg: {d.get('resource_group','?')}) → db: {dbs}")
            for db, tables in s["sql_tables"].items():
                if tables:
                    out.append(f"- Tabelle in `{db}`: {', '.join(tables)}")
            out.append("")

        # ADF
        adf = s["adf"]
        if adf["linked_services"] or adf["datasets"] or adf["pipelines"]:
            out += [f"## Azure Data Factory — `{adf.get('factory') or '?'}`", ""]
            if adf["linked_services"]:
                out += ["**Linked Services**", ""]
                for name, d in adf["linked_services"].items():
                    out.append(f"- `{name}` ({d.get('type')})")
                out.append("")
            if adf["datasets"]:
                out += ["**Datasets**", ""]
                for name, d in adf["datasets"].items():
                    out.append(
                        f"- `{name}` ({d.get('type')}) → LS `{d.get('linked_service')}`"
                        + (f", target `{d.get('target')}`" if d.get("target") else "")
                    )
                out.append("")
            if adf["pipelines"]:
                out += ["**Pipelines**", ""]
                for name, d in adf["pipelines"].items():
                    for a in d.get("activities", []):
                        io = f"{a.get('inputs')} → {a.get('outputs')}"
                        out.append(f"- `{name}` / {a.get('name')} ({a.get('type')}): {io}")
                out.append("")

        # Networking
        net = s.get("networking", {})
        if net.get("vnets") or net.get("private_endpoints") or net.get("nsgs"):
            out += ["## Networking", ""]
            for vname, vd in net.get("vnets", {}).items():
                space = ", ".join(vd.get("address_space", [])) or "?"
                subs = ", ".join(vd.get("subnets", [])) or "—"
                out.append(f"- VNet **{vname}** ({space}) — subnet: {subs}")
            for pe in net.get("private_endpoints", []):
                gids = ",".join(
                    g for c in pe.get("connections", []) for g in c.get("group_ids", [])
                )
                out.append(f"- Private Endpoint **{pe['name']}** (group: {gids or '?'})")
            for nsg in net.get("nsgs", []):
                out.append(f"- NSG {nsg['name']}")
            out.append("")

        # Note custom (persistenti, aggiunte via docs.add_note).
        notes = s.get("notes", {})
        if notes:
            out += ["## Note e documentazione aggiuntiva", ""]
            for title, content in notes.items():
                out += [f"### {title}", "", content, ""]

        out += self._render_mermaid()
        return "\n".join(out) + "\n"

    def _render_mermaid(self) -> list[str]:
        s = self.state
        lines = ["## Diagramma infrastruttura", "", "```mermaid", "graph LR"]
        has_node = False

        for acc, d in s["storage"].items():
            aid = self._nid("SA", acc)
            lines.append(f'  {aid}["Storage: {acc}"]')
            has_node = True
            for kind, label in (
                ("containers", "container"),
                ("tables", "table"),
                ("queues", "queue"),
                ("file_shares", "share"),
            ):
                for item in d.get(kind, []):
                    nid = self._nid("O", acc + "_" + item)
                    lines.append(f'  {nid}["{label}: {item}"]')
                    lines.append(f"  {aid} --> {nid}")

        for name, d in s["sql_servers"].items():
            sid = self._nid("SRV", name)
            lines.append(f'  {sid}["SQL: {name}"]')
            has_node = True
            for db in d.get("databases", []):
                did = self._nid("DB", name + "_" + db)
                lines.append(f'  {did}["db: {db}"]')
                lines.append(f"  {sid} --> {did}")

        for vname, vd in s.get("networking", {}).get("vnets", {}).items():
            vid = self._nid("VN", vname)
            lines.append(f'  {vid}["VNet: {vname}"]')
            has_node = True
            for sub in vd.get("subnets", []):
                sid = self._nid("SUB", vname + "_" + sub)
                lines.append(f'  {sid}["subnet: {sub}"]')
                lines.append(f"  {vid} --> {sid}")

        adf = s["adf"]
        for name, d in adf["linked_services"].items():
            lines.append(f'  {self._nid("LS", name)}["LS: {name}<br/>{d.get("type")}"]')
            has_node = True
        for name, d in adf["datasets"].items():
            did = self._nid("DS", name)
            lines.append(f'  {did}["DS: {name}"]')
            has_node = True
            if d.get("linked_service"):
                lines.append(f'  {did} --> {self._nid("LS", d["linked_service"])}')
        for name, d in adf["pipelines"].items():
            pid = self._nid("PL", name)
            lines.append(f'  {pid}["Pipeline: {name}"]')
            has_node = True
            for a in d.get("activities", []):
                for ds in a.get("inputs", []) + a.get("outputs", []):
                    lines.append(f'  {pid} --> {self._nid("DS", ds)}')

        if not has_node:
            lines.append("  empty[Nessuna risorsa registrata]")
        lines += ["```", ""]
        return lines
