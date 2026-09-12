"""Self-authored demonstration manuals/orders; no real product claims."""

import io

from PIL import Image, ImageDraw, ImageFont

from ..agent.fixtures import AgentFixtureGateway
from ..ingest.contracts import CorpusRegion
from ..ingest.manuals import ManualIngestor
from ..qa.gateway import GatewayReply
from ..retrieval.models import HashEmbedder
from ..retrieval.store import ScopedIndex
from ..schemas import BBox, Cost, OrderItem, Product, ProductVariant
from ..storage.snapshots import Snapshots


def seed_demo(repository, root, *, embedder=None):
    snapshot = "snapshot_web_demo"
    snapshots = Snapshots(repository)
    if snapshots.get(snapshot) and snapshots.get(snapshot)["state"] in (
        "READY",
        "ACTIVE",
    ):
        return snapshot
    snapshots.begin(snapshot, "demo", {"fixture": "web-demo-v1", "synthetic": True})
    ingestor = ManualIngestor(repository, root / "assets", snapshot, "demo")
    evidence = []
    specs = [
        (
            "a",
            "TEST-DS",
            "red",
            "POWER",
            "启动时请按一次红色 POWER 按钮。请勿拆开外壳。",
        ),
        (
            "b",
            "TEST-DS2",
            "blue",
            "CONNECT",
            "连接时请长按蓝色 CONNECT 按钮 3 秒。指示灯闪烁后松开。",
        ),
        (
            "c",
            "TEST-LITE",
            "green",
            "START",
            "启动时请按绿色 START 按钮 2 秒。请保持面板干燥。",
        ),
        ("d", "PRIVATE-A", "orange", "POWER", "这是另一位用户的示例资料。"),
        ("e", "PRIVATE-B", "purple", "START", "这是另一位用户的示例资料。"),
    ]
    for letter, model, color, label, instructions in specs:
        principal = "user_demo" if letter in "abc" else "user_other"
        product = Product(
            product_id="product_web_" + letter,
            domain="demo",
            brand="ShopGuide Lab",
            model=model,
            category="示例训练面板",
        )
        variant = ProductVariant(
            variant_id="variant_web_" + letter, product_id=product.product_id
        )
        image = Image.new("RGB", (560, 260), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=28)
        draw.text((28, 18), model + " training panel", font=font, fill="black")
        draw.ellipse((35, 87, 127, 179), fill=color)
        draw.text((155, 112), label, font=font, fill="black")
        draw.text(
            (28, 210),
            "Synthetic manual - demo only",
            font=ImageFont.load_default(size=18),
            fill="#43516a",
        )
        out = io.BytesIO()
        image.save(out, format="PNG")
        region = CorpusRegion(
            region_id="region_web_" + letter,
            kind="illustration",
            text=model + " 示例说明：" + instructions,
            original_xywh=(0.0, 0.0, 560.0, 260.0),
            bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
        )
        evidence.extend(
            ingestor.page_image(
                out.getvalue(),
                product=product,
                variant=variant,
                principal=principal,
                basis="Self-authored synthetic training manual",
                language="zh",
                regions=(region,),
            )
        )
        order = OrderItem(
            order_item_id="item_web_" + letter,
            principal_id=principal,
            product_id=product.product_id,
            variant_id=variant.variant_id,
            order_id="order_web_" + principal,
        )
        repository.put(order, order.order_item_id)
    index = ScopedIndex(
        repository, root / "assets", root / "indexes", snapshot, embedder or HashEmbedder()
    )
    index.build(evidence)
    index.publish(expected_active=snapshots.active("demo"))
    return snapshot


class WebDemoGateway(AgentFixtureGateway):
    """Explicit UI test mode; intent rules never become official model measurements."""

    def complete(self, role, request, images, schema):
        if (
            role == "plan"
            and request["product"]
            and any(
                w in request["question"] for w in ("查询工单", "工单进度", "维修进度")
            )
        ):
            import re

            found = re.search(r"ticket_[a-zA-Z0-9_-]+", request["question"])
            return GatewayReply(
                {
                    "kind": "tool",
                    "tool_name": "query_service_requests",
                    "arguments": {"ticket_id": found.group() if found else None},
                    "reason": "查询当前商品的模拟工单记录",
                },
                Cost(),
            )
        if (
            role == "plan"
            and request["product"]
            and any(word in request["question"] for word in ("工单", "售后"))
        ):
            candidates = [
                c
                for o in request["observations"]
                for c in o.get("result", {}).get("candidates", [])
                if c["product_id"] == request["product"]["product_id"]
            ]
            if candidates:
                decision = {
                    "kind": "tool",
                    "tool_name": "prepare_service_request",
                    "arguments": {
                        "reason": "用户申请模拟售后",
                        "related_order_item": candidates[0]["order_item_id"],
                        "summary": request["question"],
                    },
                    "reason": "准备待确认申请",
                }
            else:
                decision = {
                    "kind": "tool",
                    "tool_name": "list_purchased_items",
                    "arguments": {},
                    "reason": "查询当前用户的订单项",
                }
            return GatewayReply(decision, Cost())
        if role == "plan" and not request["product"]:
            return GatewayReply(
                {
                    "kind": "clarify",
                    "question": "请先选择要咨询的商品和型号。",
                    "missing_fields": ["product"],
                    "reason": "确认适用资料",
                },
                Cost(),
            )
        return super().complete(role, request, images, schema)
