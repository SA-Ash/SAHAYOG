import hashlib
import io
import json
import uuid
from collections import Counter, defaultdict
from html import escape

import qrcode
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from sqlalchemy import func, select

from app.core.audit import AuditService, canonical, digest
from app.core.config import get_settings
from app.core.errors import AppError
from app.engines.seals import merkle_proof, merkle_root
from app.models.entities import Case, now
from app.models.intelligence import (
    Attribution,
    FederatedQuery,
    FederatedReply,
    GraphNode,
    Vasp,
)
from app.models.workflow import (
    AuditLog,
    BlastRadiusReport,
    EvidenceItem,
    EvidenceSnapshot,
    FreezeRequest,
    Report,
    TaintRun,
)
from app.schemas.cases import CaseOut
from app.services.attribution import attribution_json
from app.services.cases import CaseService
from app.services.labels import label_json
from app.services.requests import request_json
from app.services.traces import graph_json, latest_trace

CERTIFICATE_SOURCE = (
    "https://www.indiacode.nic.in/indiacode/bitstream/123456789/20063/1/aa202347.pdf"
)


def report_numbers(value):
    if isinstance(value, dict):
        return {key: report_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [report_numbers(item) for item in value]
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    return value


def certificate(bundle_hash):
    return {
        "title": "Section 63 BSA certificate preparation template",
        "legal_review_required": True,
        "source": CERTIFICATE_SOURCE,
        "note": "Preparation aid only. Complete and review the statutory Schedule, including Part A and Part B, before signing or filing. No facts about lawful control, operation or admissibility are certified automatically.",
        "part_a_party": {
            "name": "[Party/certifying officer]",
            "relationship_and_address": "[Complete identity and address]",
            "record_source": "[Device or digital record source]",
            "device_details": "[Make, model, colour, serial and applicable identifiers]",
            "control_and_regular_use": "[Describe lawful control and ordinary use]",
            "operation_and_accuracy": "[Describe operation, interruptions and their effect]",
            "production_method": "[Describe how these records were produced]",
            "hash_report": "[Enclose independently checked hash report]",
            "signature_date_time_ist_place": "[Complete after review]",
        },
        "part_b_expert": {
            "name_designation_identity_address": "[Expert details]",
            "device_and_record_source_details": "[Independent examination]",
            "hash_algorithm_and_value": "SHA-256: " + bundle_hash,
            "hash_report": "[Enclose expert hash report]",
            "signature_date_time_ist_place": "[Complete after independent review]",
        },
    }


class ReportService:
    @staticmethod
    def directory():
        path = get_settings().upload_dir.parent / "reports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def build(db, case_id, data, actor):
        case = CaseService.get_case(db, case_id)
        db.scalar(select(Case).where(Case.id == case_id).with_for_update())
        trace = latest_trace(db, case_id, completed=True)
        graph = graph_json(db, trace)
        labels = []
        for node in db.scalars(select(GraphNode).where(GraphNode.trace_run_id == trace.id)):
            from app.services.labels import LabelService

            labels.extend(
                label_json(db, label)
                for label in LabelService.lookup(db, node.chain, node.address, case.scenario_id)
            )
        labels = list({label["id"]: label for label in labels}.values())
        attribution = db.scalar(select(Attribution).where(Attribution.trace_run_id == trace.id))
        taint = db.scalar(
            select(TaintRun)
            .where(TaintRun.case_id == case_id, TaintRun.trace_run_id == trace.id)
            .order_by(TaintRun.created_at.desc())
        )
        impact = db.scalar(
            select(BlastRadiusReport)
            .where(BlastRadiusReport.case_id == case_id)
            .order_by(BlastRadiusReport.created_at.desc())
        )
        if impact and (not taint or impact.taint_run_id != taint.id):
            impact = None
        requests = [
            request_json(db, r)
            for r in db.scalars(select(FreezeRequest).where(FreezeRequest.case_id == case_id))
        ]
        queries = []
        for query in db.scalars(select(FederatedQuery).where(FederatedQuery.case_id == case_id)):
            replies = [
                {
                    "vasp": db.get(Vasp, r.vasp_id).name,
                    "status": r.status,
                    "received_at": r.received_at,
                    "response_s": r.response_s,
                    "evidence": r.raw_json,
                }
                for r in db.scalars(
                    select(FederatedReply).where(FederatedReply.query_id == query.id)
                )
            ]
            queries.append(
                {
                    "id": str(query.id),
                    "mode": query.mode,
                    "sent_at": query.sent_at,
                    "replies": replies,
                }
            )
        payloads = {
            "case": CaseOut.model_validate(case).model_dump(mode="json"),
            "graph": graph,
            "labels": labels,
            "attribution": attribution_json(db, attribution) if attribution else None,
            "taint": taint.result_json if taint else None,
            "impact": impact.result_json if impact else None,
            "approvals": requests,
            "vasp_responses": queries,
            "audit_integrity": AuditService.verify(db),
        }
        items = [
            {"id": str(uuid.uuid4()), "kind": kind, "payload": json.loads(canonical(payload))}
            for kind, payload in payloads.items()
        ]
        items.extend(
            {
                "id": str(uuid.uuid4()),
                "kind": "label_snapshot",
                "payload": json.loads(canonical(label)),
            }
            for label in labels
        )
        items = report_numbers(items)
        leaves = [digest(item) for item in items]
        root = merkle_root(leaves)
        version = (
            db.scalar(select(func.max(Report.version)).where(Report.case_id == case_id)) or 0
        ) + 1
        at = now()
        bundle = {
            "schema_version": 1,
            "case_id": str(case_id),
            "version": version,
            "title": data.title,
            "created_at": at.isoformat(),
            "created_by": str(actor.id),
            "source": "simulated" if case.scenario_id else "observed",
            "evidence_items": items,
            "merkle_root": root,
            "certificate_template": certificate(
                "[Bundle hash is recorded in the seal; this placeholder avoids self-referential hashing]"
            ),
        }
        bundle = report_numbers(bundle)
        bundle_hash = digest(bundle)
        rid = uuid.uuid4()
        path = ReportService.directory() / (str(rid) + ".pdf")
        ReportService.pdf(path, bundle, bundle_hash)
        row = Report(
            id=rid,
            case_id=case_id,
            version=version,
            bundle_hash=bundle_hash,
            merkle_root=root,
            pdf_path=str(path),
            pdf_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            created_by=actor.id,
            created_at=at,
            bundle_json=bundle,
        )
        db.add(row)
        db.flush()
        for index, item in enumerate(items):
            db.add(
                EvidenceItem(
                    id=uuid.UUID(item["id"]),
                    report_id=rid,
                    kind=item["kind"],
                    leaf_hash=leaves[index],
                    position=index,
                    payload_json=item["payload"],
                )
            )
            db.add(EvidenceSnapshot(report_id=rid, kind=item["kind"], payload_json=item["payload"]))
        AuditService.append(
            db,
            actor.id,
            "report.sealed",
            {"case_id": str(case_id), "report_id": str(rid), "bundle_hash": bundle_hash},
            entity="report",
            entity_id=rid,
        )
        db.commit()
        return {
            "id": str(row.id),
            "version": version,
            "bundle_hash": bundle_hash,
            "merkle_root": root,
            "created_at": at,
            "items": [{"id": item["id"], "kind": item["kind"]} for item in items],
            "pdf_url": f"/api/v1/reports/{rid}/pdf",
            "bundle_url": f"/api/v1/reports/{rid}/bundle.json",
            "verify_url": get_settings().frontend_origin + "/verify/" + bundle_hash,
        }

    @staticmethod
    def pdf(path, bundle, bundle_hash):
        styles = getSampleStyleSheet()
        for style in ["Title", "Heading1", "Heading2", "Heading3"]:
            styles[style].keepWithNext = True
        styles["BodyText"].fontSize, styles["BodyText"].leading = 9, 13
        styles["BodyText"].wordWrap = "CJK"
        story = []

        def paragraph(text, style="BodyText"):
            story.append(Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style]))
            spacing = Spacer(1, 3 * mm)
            spacing.keepWithNext = style.startswith("Heading") or style == "Title"
            story.append(spacing)

        def json_sections(value, depth=0):
            if isinstance(value, dict):
                simple = []
                for key, item in value.items():
                    if not isinstance(item, (dict, list)):
                        simple.append(key.replace("_", " ") + ": " + str(item))
                if simple:
                    paragraph("\n".join(simple))
                for key, item in value.items():
                    if isinstance(item, (dict, list)):
                        paragraph(key.replace("_", " ").title(), "Heading3")
                        json_sections(item, depth + 1)
            elif isinstance(value, list):
                if not value:
                    paragraph("None recorded")
                for index, item in enumerate(value):
                    if len(value) > 1:
                        paragraph("Record " + str(index + 1), "Heading3")
                    json_sections(item, depth + 1)
            else:
                paragraph(value if value is not None else "Not available at report date")

        paragraph("SAHYOG | SEALED EVIDENCE", "Title")
        paragraph(bundle["title"], "Heading1")
        paragraph(
            "Version "
            + str(bundle["version"])
            + " | "
            + bundle["created_at"]
            + " | "
            + bundle["source"].upper()
        )
        paragraph("SHA-256: " + bundle_hash)
        paragraph("Merkle root: " + bundle["merkle_root"])
        verification = (
            get_settings().frontend_origin
            + "/verify/"
            + bundle_hash
            + "?root="
            + bundle["merkle_root"]
        )
        qr = io.BytesIO()
        qrcode.make(verification).save(qr, format="PNG")
        qr.seek(0)
        story.append(Image(qr, width=35 * mm, height=35 * mm))
        paragraph(verification)
        paragraph(
            "This seal proves content integrity, not the truth of an attribution or legal authority to freeze. Synthetic data is explicitly identified. Amounts are exact base units with asset precision."
        )
        for item in bundle["evidence_items"]:
            if item["kind"] == "label_snapshot":
                continue
            if item["kind"] in {"case", "graph"}:
                story.append(PageBreak())
            else:
                story.append(Spacer(1, 6 * mm))
            paragraph(item["kind"].replace("_", " ").title(), "Heading1")
            if item["kind"] == "graph":
                graph = item["payload"]
                paragraph("Observed transaction path; persisted trace version")
                nodes = {node["data"]["id"]: node["data"] for node in graph.get("nodes", [])}
                for index, edge in enumerate(graph.get("edges", [])):
                    d = edge["data"]
                    source, target = nodes.get(d["source"], {}), nodes.get(d["target"], {})
                    paragraph("Transfer " + str(index + 1), "Heading3")
                    paragraph(
                        f"From: {source.get('chain')} / {source.get('address')}\n"
                        f"To: {target.get('chain')} / {target.get('address')}\n"
                        f"Amount: {d.get('amount')} base units {d.get('token')} (decimals {d.get('decimals')})\n"
                        f"Transaction: {d.get('tx_hash', 'inferred protocol link')} / log {d.get('log_index', 'n/a')}\n"
                        f"Time: {d.get('time')} | Inferred: {d.get('inferred', False)}"
                    )
                    if d.get("inferred"):
                        json_sections(d.get("evidence", []))
                paragraph("Address roles and trace limits", "Heading2")
                for node in nodes.values():
                    paragraph(
                        f"{node.get('chain')} / {node.get('address')}\nRole: {node.get('role')} | Hop: {node.get('hop')}"
                    )
                json_sections(graph.get("stop_summary", {}))
            elif item["kind"] == "taint" and item["payload"]:
                taint = item["payload"]
                json_sections(
                    {
                        key: value
                        for key, value in taint.items()
                        if key not in {"flows", "balances", "evidence", "by_case", "ranges"}
                    }
                )
                rows = [
                    ["Case", "Traceable base units", "FIFO/Haircut minimum", "FIFO/Haircut maximum"]
                ]
                rows.extend(
                    [
                        case_id,
                        amount,
                        taint["ranges"][case_id]["min"],
                        taint["ranges"][case_id]["max"],
                    ]
                    for case_id, amount in taint["by_case"].items()
                )
                table = LongTable(
                    [
                        [Paragraph(escape(str(cell)), styles["BodyText"]) for cell in row]
                        for row in rows
                    ],
                    colWidths=[65 * mm, 36 * mm, 36 * mm, 37 * mm],
                    repeatRows=1,
                )
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5eef2")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b7c6ce")),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]
                    )
                )
                story.append(table)
                for evidence in taint.get("evidence", []):
                    json_sections(
                        {
                            **{
                                key: value
                                for key, value in evidence.items()
                                if key != "opening_funds"
                            },
                            "unobserved_opening_funding_count": len(
                                evidence.get("opening_funds", [])
                            ),
                        }
                    )
                paragraph(
                    "Complete balance and flow ledgers are preserved in the matching JSON bundle."
                )
            elif item["kind"] == "approvals":
                for request in item["payload"]:
                    json_sections(
                        {key: value for key, value in request.items() if key != "package"}
                    )
                if not item["payload"]:
                    paragraph("No requests recorded at report date")
            else:
                json_sections(item["payload"])
        story.append(PageBreak())
        paragraph("Section 63 BSA certificate preparation template", "Heading1")
        json_sections(certificate(bundle_hash))

        def footer(canvas, document):
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#526577"))
            canvas.drawString(18 * mm, 12 * mm, "SAHYOG | " + bundle_hash[:20])
            canvas.drawRightString(192 * mm, 12 * mm, "Page " + str(document.page))

        SimpleDocTemplate(
            str(path),
            pagesize=(210 * mm, 297 * mm),
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=20 * mm,
        ).build(story, onFirstPage=footer, onLaterPages=footer)

    @staticmethod
    def verify(db, bundle_hash, supplied=None):
        row = db.scalar(select(Report).where(Report.bundle_hash == bundle_hash))
        if not row:
            raise AppError("REPORT_NOT_FOUND", "No published seal has this hash", 404)
        items = list(
            db.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.report_id == row.id)
                .order_by(EvidenceItem.position)
            )
        )
        payload = [
            {"id": str(item.id), "kind": item.kind, "payload": item.payload_json} for item in items
        ]
        leaves = [digest(item) for item in payload]
        snapshots = list(
            db.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.report_id == row.id))
        )
        snapshot_valid = Counter((s.kind, canonical(s.payload_json)) for s in snapshots) == Counter(
            (item["kind"], canonical(item["payload"])) for item in payload
        )
        valid = (
            digest(row.bundle_json) == bundle_hash
            and merkle_root(leaves) == row.merkle_root
            and payload == row.bundle_json["evidence_items"]
            and all(digest(item) == stored.leaf_hash for item, stored in zip(payload, items))
            and snapshot_valid
        )
        if supplied is not None:
            valid = valid and digest(supplied) == bundle_hash
        return {
            "valid": valid,
            "bundle_hash": bundle_hash,
            "merkle_root": row.merkle_root,
            "version": row.version,
            "created_at": row.created_at,
            "item_count": len(items),
            "note": "Seal verification does not certify the truth or legal admissibility of the records.",
        }

    @staticmethod
    def proof(db, report_id, item_id):
        row = db.get(Report, report_id)
        if not row:
            raise AppError("REPORT_NOT_FOUND", "Report not found", 404)
        items = list(
            db.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.report_id == report_id)
                .order_by(EvidenceItem.position)
            )
        )
        item = next((item for item in items if item.id == item_id), None)
        if not item:
            raise AppError("EVIDENCE_ITEM_NOT_FOUND", "Evidence item not found", 404)
        return {
            "item_id": str(item.id),
            "kind": item.kind,
            "leaf": item.leaf_hash,
            "path": merkle_proof([i.leaf_hash for i in items], item.position),
            "root": row.merkle_root,
            "bundle_hash": row.bundle_hash,
        }

    @staticmethod
    def overview(db):
        totals = defaultdict(lambda: {"traced": 0, "frozen": 0})
        counts = defaultdict(int)
        durations = []
        for case in db.scalars(select(Case)):
            counts[case.status] += 1
            run = db.scalar(
                select(TaintRun)
                .where(TaintRun.case_id == case.id)
                .order_by(TaintRun.created_at.desc())
            )
            if run:
                asset = run.result_json["target"]
                key = (asset["chain"], asset["token"], asset["token_address"], asset["decimals"])
                totals[key]["traced"] += int(run.result_json["by_case"].get(str(case.id), 0))
            attributed_at = next(
                (
                    entry.at
                    for entry in db.scalars(
                        select(AuditLog)
                        .where(AuditLog.action == "case.attributed")
                        .order_by(AuditLog.at)
                    )
                    if entry.payload_json.get("case_id") == str(case.id)
                    and entry.payload_json.get("status") in {"inferred", "confirmed"}
                ),
                None,
            )
            if attributed_at:
                durations.append(
                    {
                        "case_ref": case.case_ref,
                        "seconds": max(0, (attributed_at - case.created_at).total_seconds()),
                    }
                )
        for request in db.scalars(select(FreezeRequest).where(FreezeRequest.state == "FROZEN")):
            target = request.package_json.get("impact", {}).get("target")
            if target:
                key = (
                    target["chain"],
                    target["token"],
                    target["token_address"],
                    target["decimals"],
                )
                totals[key]["frozen"] += sum(
                    int(value) for value in request.amount_by_case_json.values()
                )
        return {
            "case_count": sum(counts.values()),
            "statuses": dict(counts),
            "assets": [
                {
                    "chain": key[0],
                    "token": key[1],
                    "token_address": key[2],
                    "decimals": key[3],
                    "traced": str(value["traced"]),
                    "frozen": str(value["frozen"]),
                }
                for key, value in totals.items()
            ],
            "time_to_attribution": durations,
            "average_time_to_attribution_seconds": sum(d["seconds"] for d in durations)
            / len(durations)
            if durations
            else None,
            "evidence": [
                {
                    "signal": "asset_separated_analytics",
                    "note": "Currencies and token contracts are never summed together. Frozen values use current request state.",
                }
            ],
        }
