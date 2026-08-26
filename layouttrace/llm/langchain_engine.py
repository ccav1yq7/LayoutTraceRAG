"""Production LLM engine backed by a LangChain chat model (OpenAI / Anthropic)."""
from __future__ import annotations

from ..types import EvidenceNode, GradedNode
from .base import LLMEngine


def _build_chat(provider: str, model: str):
    # Generous retries + timeout so transient 429/5xx from the provider (or an
    # OpenAI-compatible proxy) back off and recover instead of crashing a long run.
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0, max_retries=8, timeout=90)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, temperature=0, max_retries=8, timeout=90)


def _format_evidence(evidence: list[EvidenceNode]) -> str:
    return "\n".join(
        f"- [{n.timecode}] ({n.modality}) {n.text.strip()}" for n in evidence
    )


class LangChainEngine(LLMEngine):
    def __init__(self, provider: str = "openai", model: str = "gpt-4o-mini") -> None:
        self._chat = _build_chat(provider, model)

    def _ask(self, system: str, user: str) -> str:
        msg = self._chat.invoke([("system", system), ("user", user)])
        return (msg.content if hasattr(msg, "content") else str(msg)).strip()

    def plan(self, question: str) -> str:
        out = self._ask(
            "你是长视频问答的检索规划器。把用户问题改写成一句适合向量+关键词检索的查询，只输出查询本身。",
            question,
        )
        return out or question

    def expand_query(self, question: str) -> list[str]:
        alts = self._ask(
            "为下面的问题生成 2 个不同措辞的检索查询，外加 1 句"
            "「假设答案」(HyDE)。每行一个，共 3 行，不要编号。",
            question,
        )
        out = [question.strip()] + [l.strip("-• \t") for l in alts.splitlines() if l.strip()]
        seen, uniq = set(), []
        for q in out:
            if q and q not in seen:
                seen.add(q)
                uniq.append(q)
        return uniq[:4]

    def grade(self, question: str, evidence: list[EvidenceNode]) -> bool:
        out = self._ask(
            "判断给定证据是否足以回答问题。只回答 YES 或 NO。",
            f"问题：{question}\n证据：\n{_format_evidence(evidence)}",
        )
        return out.strip().upper().startswith("YES")

    def evaluate_evidence(
        self, question: str, evidence: list[EvidenceNode]
    ) -> list[GradedNode]:
        if not evidence:
            return []
        numbered = "\n".join(f"{i}. {n.text.strip()}" for i, n in enumerate(evidence))
        out = self._ask(
            "对每条证据判断与问题的相关性:correct(可直接支撑)/ambiguous(部分相关)/"
            "incorrect(无关)。每行输出 `序号:标签`,不要多余文字。",
            f"问题：{question}\n证据：\n{numbered}",
        )
        labels: dict[int, str] = {}
        for line in out.splitlines():
            if ":" in line or "：" in line:
                left, _, right = line.replace("：", ":").partition(":")
                try:
                    idx = int(left.strip().split(".")[0])
                except ValueError:
                    continue
                lab = right.strip().lower()
                if lab in {"correct", "ambiguous", "incorrect"}:
                    labels[idx] = lab
        graded = []
        for i, n in enumerate(evidence):
            lab = labels.get(i, "ambiguous")
            graded.append(GradedNode(node=n, label=lab,
                                     score=1.0 if lab == "correct" else 0.5 if lab == "ambiguous" else 0.0))
        return graded

    def refine(self, question: str, evidence: list[EvidenceNode], prev_query: str) -> str:
        out = self._ask(
            "现有证据不足以回答问题。基于信息缺口生成一句更好的检索查询，只输出查询本身。",
            f"问题：{question}\n上次查询：{prev_query}\n已有证据：\n{_format_evidence(evidence)}",
        )
        return out or prev_query

    def supported(self, claim: str, evidence: list[EvidenceNode]) -> bool:
        out = self._ask(
            "判断「论断」是否被给定证据支撑。只回答 YES 或 NO。",
            f"论断：{claim}\n证据：\n{_format_evidence(evidence)}",
        )
        return out.strip().upper().startswith("YES")

    def generate(self, question: str, evidence: list[EvidenceNode]) -> str:
        return self._ask(
            "你是长视频问答助手。只依据给定证据回答，并在每个论断后用 [HH:MM:SS-HH:MM:SS] "
            "标注来源时间码；证据不足时明说不足，不要编造。",
            f"问题：{question}\n证据（含时间码）：\n{_format_evidence(evidence)}",
        )
