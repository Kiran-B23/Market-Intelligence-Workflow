"""Proposed vendors for the mute `from_sheet` entries, each verified by fetching.

A proposal is my recall; the system's rule is that a guessed host which happens to
answer is invented evidence. So every one is checked: the domain must resolve, answer
200, and the page must NAME the tool. Anything that fails is not written.
"""
# canonical_name -> (kind, homepage, [official_domains], vendor, [name terms to find])
PROPOSED = {
 "Telegram":        ("service","https://telegram.org",["telegram.org","core.telegram.org","web.telegram.org"],"Telegram",["telegram"]),
 "Google Sheets":   ("service","https://workspace.google.com/products/sheets/",["google.com","workspace.google.com","developers.google.com","sheets.google.com"],"Google",["sheets"]),
 "Google Colab":    ("service","https://colab.research.google.com",["colab.research.google.com","research.google.com","google.com"],"Google",["colab"]),
 "Claude Code":     ("tool","https://claude.com/product/claude-code",["claude.com","anthropic.com","docs.claude.com"],"Anthropic",["claude code","claude"]),
 "Google Docs":     ("service","https://workspace.google.com/products/docs/",["google.com","workspace.google.com","developers.google.com","docs.google.com"],"Google",["docs"]),
 "LinkedIn":        ("service","https://www.linkedin.com",["linkedin.com","developer.linkedin.com"],"LinkedIn",["linkedin"]),
 "Google Calendar": ("service","https://workspace.google.com/products/calendar/",["google.com","workspace.google.com","developers.google.com","calendar.google.com"],"Google",["calendar"]),
 "Whisper":         ("model","https://openai.com/index/whisper/",["openai.com","platform.openai.com","github.com"],"OpenAI",["whisper"]),
 "ChromaDB":        ("service","https://www.trychroma.com",["trychroma.com","docs.trychroma.com"],"Chroma",["chroma"]),
 "Gamma AI":        ("service","https://gamma.app",["gamma.app"],"Gamma",["gamma"]),
 "Otter AI":        ("service","https://otter.ai",["otter.ai"],"Otter.ai",["otter"]),
 "anthropic":       ("package","https://www.anthropic.com",["anthropic.com","docs.anthropic.com","docs.claude.com","claude.com"],"Anthropic",["anthropic","claude"]),
 "Tavily Search":   ("service","https://tavily.com",["tavily.com","docs.tavily.com"],"Tavily",["tavily"]),
 "Automatic1111":   ("tool","https://github.com/AUTOMATIC1111/stable-diffusion-webui",["github.com"],"AUTOMATIC1111",["stable diffusion","automatic1111"]),
 "Google Drive":    ("service","https://workspace.google.com/products/drive/",["google.com","workspace.google.com","developers.google.com","drive.google.com"],"Google",["drive"]),
 "Gmail":           ("service","https://workspace.google.com/products/gmail/",["google.com","workspace.google.com","developers.google.com","mail.google.com"],"Google",["gmail"]),
 "DeepSeek":        ("service","https://www.deepseek.com",["deepseek.com","api-docs.deepseek.com","platform.deepseek.com"],"DeepSeek",["deepseek"]),
 "LM Studio":       ("tool","https://lmstudio.ai",["lmstudio.ai"],"LM Studio",["lm studio","lmstudio"]),
 "Julius AI":       ("service","https://julius.ai",["julius.ai"],"Julius",["julius"]),
 "Hugging Face Spaces": ("service","https://huggingface.co/spaces",["huggingface.co"],"Hugging Face",["spaces","hugging face"]),
 "Mistral":         ("service","https://mistral.ai",["mistral.ai","docs.mistral.ai","console.mistral.ai"],"Mistral AI",["mistral"]),
 "Langflow":        ("tool","https://www.langflow.org",["langflow.org","docs.langflow.org"],"Langflow",["langflow"]),
 "V0 by Vercel":    ("service","https://v0.app",["v0.app","v0.dev","vercel.com"],"Vercel",["v0"]),
 "Replit":          ("service","https://replit.com",["replit.com","docs.replit.com"],"Replit",["replit"]),
 "Cursor IDE":      ("tool","https://cursor.com",["cursor.com","docs.cursor.com"],"Cursor",["cursor"]),
 "Windsurf":        ("tool","https://windsurf.com",["windsurf.com","docs.windsurf.com"],"Windsurf",["windsurf"]),
 "MeloTTS":         ("tool","https://github.com/myshell-ai/MeloTTS",["github.com"],"MyShell",["melotts"]),
 "OpenVoice V2":    ("tool","https://github.com/myshell-ai/OpenVoice",["github.com"],"MyShell",["openvoice"]),
 "Base44":          ("service","https://base44.com",["base44.com"],"Base44",["base44"]),
 "Google Antigravity": ("tool","https://antigravity.google",["antigravity.google","google.com"],"Google",["antigravity"]),
 "Gemini Guided Learning": ("service","https://gemini.google.com",["gemini.google.com","google.com"],"Google",["gemini"]),
 "ProtectAI":       ("service","https://protectai.com",["protectai.com","huggingface.co"],"Protect AI",["protect ai","protectai"]),
 "PowerPoint":      ("tool","https://www.microsoft.com/microsoft-365/powerpoint",["microsoft.com","office.com"],"Microsoft",["powerpoint"]),
 "Perplexity":      ("service","https://www.perplexity.ai",["perplexity.ai","docs.perplexity.ai"],"Perplexity",["perplexity"]),
 "Notion":          ("service","https://www.notion.com",["notion.com","notion.so","developers.notion.com"],"Notion",["notion"]),
 "Canva":           ("service","https://www.canva.com",["canva.com"],"Canva",["canva"]),
 "Figma":           ("service","https://www.figma.com",["figma.com"],"Figma",["figma"]),
 "Slack":           ("service","https://slack.com",["slack.com","api.slack.com"],"Slack",["slack"]),
 "Zapier":          ("service","https://zapier.com",["zapier.com"],"Zapier",["zapier"]),
 "Make":            ("service","https://www.make.com",["make.com"],"Make",["make"]),
 "ElevenLabs":      ("service","https://elevenlabs.io",["elevenlabs.io"],"ElevenLabs",["elevenlabs","eleven labs"]),
 "Pinecone":        ("service","https://www.pinecone.io",["pinecone.io","docs.pinecone.io"],"Pinecone",["pinecone"]),
 "Qdrant":          ("service","https://qdrant.tech",["qdrant.tech"],"Qdrant",["qdrant"]),
 "Streamlit":       ("tool","https://streamlit.io",["streamlit.io","docs.streamlit.io"],"Streamlit",["streamlit"]),
 "Ollama":          ("tool","https://ollama.com",["ollama.com"],"Ollama",["ollama"]),
}

# Not products. Seeding a vendor for these would be inventing one.
NOT_A_TOOL = {
 "QLoRA": "technique — a fine-tuning method from a paper, with no vendor or release",
 "RSS Feed": "format — a syndication standard, not a product anyone ships",
 "InMemorySaver": "library class — a LangGraph checkpointer, part of langgraph",
 "PaySim": "dataset — a synthetic transaction simulator from a paper",
 "Cifer Fraud Detection Dataset": "dataset — a published data file, not a service",
 "SEBI Financial Education Booklet": "document — a regulator's PDF, not a tool",
 "Stable Diffusion 3.5": "model — belongs to the Stability AI entry",
 "Claude Skills": "product feature — documented under Anthropic's own entry",
}

# One tool, several spellings. The from_sheet name aliases into the entry that has
# the domains rather than becoming a second mute entry.
ALIAS_OF = {
 "Murf AI": "Murf.AI",
 "gemini-api": "Google",
 "gemini": "Google",
 "groq-api": "Groq",
}


# ---------------------------------------------------------------------------
# Verify, then apply. Run from the repo root:
#     python3 tools/seed_vendors.py            # verify only, writes nothing
#     python3 tools/seed_vendors.py --apply    # write the confirmed ones
#
# Re-runnable: an entry that already has domains is skipped, so this never
# overwrites a human's work and never needs to be run "just once".

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def verify(proposals: dict) -> dict:
    """Fetch each proposal and confirm the page names the tool."""
    from miw import net
    tag = re.compile(r"<[^>]+>")
    out = {}
    for name, (_kind, home, _doms, _vendor, terms) in proposals.items():
        f = net.fetch(home)
        ok, why = False, ""
        if not f.reachable:
            why = f"unreachable: {f.error or f.status}"
        elif f.gone:
            why = str(f.status)
        elif f.blocked:
            # Our own blocked request is not evidence the domain is wrong - the same
            # distinction `net.Fetch` and the nomination ladder draw. Retry on a host
            # the vendor publishes docs on.
            why = f"blocked ({f.status}); try a docs host"
        else:
            text = tag.sub(" ", (f.body or "")[:200_000]).lower()
            hit = [t for t in terms if t in text]
            ok, why = bool(hit), (f"names {hit[0]!r}" if hit
                                  else "served but does not name the tool")
        out[name] = {"ok": ok, "why": why, "status": f.status}
        print(f"  {'PASS' if ok else 'FAIL'}  {name:26} {str(f.status):5} {why}")
    return out


def apply(confirmed: set) -> None:
    from miw.registry import Registry
    reg = Registry.load()
    seeded = marked = aliased = 0
    for name, (kind, home, doms, vendor, _t) in PROPOSED.items():
        e = next((x for x in reg.entries.values() if x.canonical_name == name), None)
        if name not in confirmed or e is None or e.official_domains:
            continue
        e.kind, e.homepage, e.vendor = kind, home, vendor
        e.official_domains = sorted(set(doms))
        e.review_status = "verified"
        seeded += 1
    for name, why in NOT_A_TOOL.items():
        e = next((x for x in reg.entries.values() if x.canonical_name == name), None)
        if e is None or e.official_domains:
            continue
        e.watch_tier, e.review_status = "mention-only", "not-a-tool"
        e.notes = f"deliberately unmonitored: {why}"
        marked += 1
    for src_name, dst_name in ALIAS_OF.items():
        src = next((x for x in reg.entries.values() if x.canonical_name == src_name), None)
        dst = next((x for x in reg.entries.values() if x.canonical_name == dst_name), None)
        if src is None or dst is None or src is dst or src.official_domains:
            continue
        dst.aliases = sorted(set(dst.aliases) | set(src.aliases) | {src_name})
        del reg.entries[src.key]
        aliased += 1
    reg.save()
    print(f"\nseeded {seeded} · not-a-tool {marked} · aliased {aliased}")


if __name__ == "__main__":
    res = verify(PROPOSED)
    print(f"\n{sum(1 for v in res.values() if v['ok'])}/{len(res)} verified")
    if "--apply" in sys.argv:
        apply({n for n, v in res.items() if v["ok"]})
    else:
        print("nothing written; pass --apply to write the confirmed ones")
