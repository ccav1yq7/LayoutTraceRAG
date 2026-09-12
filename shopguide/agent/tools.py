import json

from ..qa.context import evidence_view, source_page
from ..schemas import Contract, Evidence, Product, ToolAction
from ..storage.repository import AssetRepository
from .contracts import (
    CatalogArgs,
    CommitArgs,
    ImageObservation,
    InspectArgs,
    PageArgs,
    QueryServiceArgs,
    SearchArgs,
    ServiceArgs,
    UploadInspectArgs,
)
from .ledger import SimulatedService, ToolLedger

SCHEMAS: dict[str, type[Contract]] = {
    "list_purchased_items": CatalogArgs,
    "resolve_product": CatalogArgs,
    "search_manuals": SearchArgs,
    "read_page": PageArgs,
    "inspect_asset": InspectArgs,
    "inspect_user_image": UploadInspectArgs,
    "prepare_service_request": ServiceArgs,
    "commit_service_request": CommitArgs,
    "query_service_requests": QueryServiceArgs,
}


READ_TOOLS = frozenset(
    {
        "list_purchased_items",
        "resolve_product",
        "search_manuals",
        "read_page",
        "inspect_asset",
        "inspect_user_image",
        "query_service_requests",
    }
)


def normalize_read_metadata(action: ToolAction) -> ToolAction:
    """Allow only redundant textual reason metadata on known read-only tools."""
    if (
        action.tool_name in READ_TOOLS
        and "reason" in action.arguments
        and (
            action.arguments["reason"] is None
            or isinstance(action.arguments["reason"], str)
        )
    ):
        arguments = dict(action.arguments)
        arguments.pop("reason")
        return action.model_copy(update={"arguments": arguments})
    return action


class AgentTools:
    def __init__(self, sessions, principal, state, index, reranker, gateway, budget):
        self.sessions = sessions
        self.repo = sessions.repository
        self.principal = principal
        self.state = state
        self.index = index
        self.reranker = reranker
        self.gateway = gateway
        self.budget = budget
        self.ledger = ToolLedger(sessions)
        self.service = SimulatedService(sessions)
        self.assets = AssetRepository(self.repo, index.asset_root)

    def scope(self):
        session = self.sessions.get(self.state["session_id"], self.principal)
        if session["task"] != self.state["task_id"]:
            raise PermissionError("TASK_CHANGED")
        if not session["product"]:
            raise PermissionError("PRODUCT_CONFIRMATION_REQUIRED")
        return self.repo.scope(
            self.principal, session["product"], session["variant"], session["snapshot"]
        )

    def validate_evidence(self, eid):
        scope = self.scope()
        ev = self.repo.get(Evidence, eid)
        if (
            ev.scope.principal_id != scope.principal_id
            or ev.scope.domain != scope.domain
            or ev.scope.snapshot_id != scope.snapshot_id
            or ev.scope.confirmed_variant != scope.confirmed_variant
            or not set(ev.scope.authorized_product_ids)
            <= set(scope.authorized_product_ids)
            or not set(ev.scope.allowed_doc_version_ids)
            <= set(scope.allowed_doc_version_ids)
        ):
            raise PermissionError("EVIDENCE_FORBIDDEN")
        self.repo.authorize(ev.scope)
        return ev

    def _evidence_result(self, items):
        pages = set(self.state["visited_page_ids"])
        accepted = []
        for ev in items:
            self.validate_evidence(ev.evidence_id)
            page = source_page(ev)
            if page not in pages and len(pages) >= self.budget.max_pages:
                continue
            pages.add(page)
            view = evidence_view(ev)
            view["text"] = view["text"][:1200]
            accepted.append(view)
            if len(accepted) >= 8:
                break
        unavailable = []
        for aid in dict.fromkeys(a for view in accepted for a in view["asset_ids"]):
            try:
                self.assets.read(aid, self.scope())
            except FileNotFoundError:
                unavailable.append(aid)
        return {
            "status": "ok" if accepted else "empty",
            "evidence": accepted,
            "media_issues": ["IMAGE_UNAVAILABLE"] if unavailable else [],
            "unavailable_asset_ids": unavailable,
        }

    def _invoke(self, name, args):
        if name in ("list_purchased_items", "resolve_product"):
            orders = self.repo.orders(self.principal)
            candidates = []
            for order in orders:
                product = self.repo.get(Product, order.product_id)
                if args.query and args.query.casefold() not in {
                    product.model.casefold(),
                    product.brand.casefold(),
                    *(a.casefold() for a in product.aliases),
                }:
                    continue
                candidates.append(
                    {
                        "order_item_id": order.order_item_id,
                        "product_id": product.product_id,
                        "variant_id": order.variant_id,
                        "brand": product.brand,
                        "model": product.model,
                    }
                )
            return {
                "status": "ok" if candidates else "empty",
                "candidates": candidates[:10],
                "requires_user_selection": True,
            }
        scope = self.scope()
        if name == "search_manuals":
            hits = self.index.search(
                args.query,
                scope,
                reranker=self.reranker,
                k=args.top_k,
                require_assets=False,
            )
            return self._evidence_result([h["evidence"] for h in hits])
        if name == "read_page":
            # Same authorized page primitive as fixed B2; neighbor expansion stays in scope.
            from ..qa.fixed import FixedRAG

            reader = FixedRAG(self.index, self.reranker, self.gateway, baseline="B1")
            items = reader._page(scope, args.page_id)
            if args.include_neighbors:
                position = items[0].source_locator.page_index_0based
                neighbors = {}
                for row in (
                    self.index._table()
                    .search()
                    .where(self.index._filter(scope))
                    .limit(None)
                    .to_list()
                ):
                    ev = self.validate_evidence(row["id"])
                    if (
                        abs(ev.source_locator.page_index_0based - position) == 1
                        and ev.source_locator.region_id is None
                    ):
                        neighbors[source_page(ev)] = ev
                items = items[:1] + list(neighbors.values()) + items[1:]
            return self._evidence_result(items)
        if name == "inspect_asset":
            allowed = {
                a
                for eid in self.state["evidence_ids"]
                for a in self.validate_evidence(eid).asset_ids
            }
            if args.asset_id not in allowed:
                raise PermissionError("ASSET_NOT_RETRIEVED")
            raw = self.assets.read(args.asset_id, scope)
            reply = self.gateway.complete(
                "inspect",
                {"question": args.question, "asset_id": args.asset_id},
                {args.asset_id: raw},
                ImageObservation.model_json_schema(),
            )
            observation = ImageObservation.model_validate_json(
                json.dumps(reply.payload)
            )
            return {
                "status": "ok",
                "asset_id": args.asset_id,
                "observation": observation.model_dump(mode="json"),
            }
        if name == "inspect_user_image":
            from ..api.uploads import Uploads

            if args.upload_id not in self.state.get("available_upload_ids", []):
                raise PermissionError("UPLOAD_NOT_ATTACHED")
            raw = Uploads(self.repo, self.index.asset_root.parent / "uploads").read(
                args.upload_id,
                self.principal,
                self.state["session_id"],
                self.state["task_id"],
            )
            reply = self.gateway.complete(
                "inspect",
                {
                    "question": args.question,
                    "source_kind": "user_upload",
                    "upload_id": args.upload_id,
                },
                {args.upload_id: raw},
                ImageObservation.model_json_schema(),
            )
            observation = ImageObservation.model_validate_json(
                json.dumps(reply.payload)
            )
            return {
                "status": "ok",
                "upload_id": args.upload_id,
                "source_kind": "user_upload",
                "observation": observation.model_dump(mode="json"),
            }
        if name == "query_service_requests":
            return self.service.query(
                self.state["run_id"], self.principal, args.ticket_id
            )
        if name == "prepare_service_request":
            orders = self.repo.orders(self.principal)
            if args.related_order_item not in {o.order_item_id for o in orders}:
                raise ValueError("ORDER_ITEM_ID_REQUIRED")
            return self.service.prepare(
                self.state["run_id"], self.principal, args.model_dump(mode="json")
            )
        if name == "commit_service_request":
            return self.service.commit(
                args.confirmation_id, self.principal, self.state["run_id"]
            )
        raise ValueError("unknown tool")

    def execute(self, action):
        action = normalize_read_metadata(action)
        if action.tool_name not in SCHEMAS:
            raise ValueError("UNKNOWN_TOOL")
        args = SCHEMAS[action.tool_name].model_validate_json(
            json.dumps(action.arguments)
        )
        if action.tool_name == "prepare_service_request" and (
            not isinstance(args, ServiceArgs)
            or args.related_order_item
            not in {o.order_item_id for o in self.repo.orders(self.principal)}
        ):
            raise ValueError("ORDER_ITEM_ID_REQUIRED")
        if action.tool_name not in ("list_purchased_items", "resolve_product"):
            self.scope()
        if (
            action.tool_name in {"prepare_service_request", "commit_service_request"}
            and self.state.get("intent_state") is not None
        ):
            understanding = self.state["intent_state"]
            if (
                not self.state.get("intent_ready")
                or self.state.get("intent_uncertain", False)
                or understanding.get("ambiguities")
                or not any(
                    g["intent_id"] == "service.apply"
                    and g["status"] == "active"
                    and g["expression"] == "current"
                    and gid not in self.state.get("fulfilled_business_goals", {})
                    for gid, g in understanding["goals"].items()
                )
            ):
                raise ValueError("INTENT_DOES_NOT_ALLOW_SERVICE_REQUEST")
        confirmation = None
        if action.tool_name == "commit_service_request":
            if not isinstance(args, CommitArgs):
                raise TypeError("invalid commit arguments")
            confirmation = args.confirmation_id
            self.service.check_approved(
                confirmation, self.principal, self.state["run_id"]
            )
        result, executed, call_id = self.ledger.execute(
            self.state["run_id"],
            action.tool_name,
            args.model_dump(mode="json"),
            lambda: self._invoke(action.tool_name, args),
            side_effect=action.tool_name == "commit_service_request",
            recover=(lambda: self.service.recover(confirmation, self.principal))
            if action.tool_name == "commit_service_request"
            else None,
        )
        # Cached observations must be reauthorized before being returned to the planner.
        for item in result.get("evidence", []):
            self.validate_evidence(item["evidence_id"])
        if result.get("asset_id"):
            self.assets.read(result["asset_id"], self.scope())
        if result.get("upload_id"):
            from ..api.uploads import Uploads

            Uploads(self.repo, self.index.asset_root.parent / "uploads").read(
                result["upload_id"],
                self.principal,
                self.state["session_id"],
                self.state["task_id"],
            )
        return result, executed, call_id
