from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import markdown
import os

router = APIRouter(prefix="/system", tags=["Documentation"])

DOCS_PATH = "docs"

def load_md(filename):
    try:
        with open(os.path.join(DOCS_PATH, filename), "r", encoding="utf-8") as f:
            return markdown.markdown(f.read(), extensions=['fenced_code', 'tables'])
    except FileNotFoundError:
        return "<h1>Document Not Found</h1>"

@router.get("/docs/internal", response_class=HTMLResponse)
async def get_internal_docs():
    # Renders all internal specs into a single view
    css = """
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; padding: 20px; max-width: 900px; margin: 0 auto; color: #333; }
        h1, h2, h3 { color: #111; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }
        code { background-color: #f6f8fa; padding: 0.2rem 0.4rem; border-radius: 3px; font-family: monospace; }
        pre { background-color: #f6f8fa; padding: 16px; overflow: auto; border-radius: 6px; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
        th, td { border: 1px solid #dfe2e5; padding: 6px 13px; }
        th { background-color: #f6f8fa; }
        .nav { margin-bottom: 30px; padding: 10px; background: #f0f0f0; border-radius: 8px; }
        .nav a { margin-right: 15px; color: #0366d6; text-decoration: none; font-weight: bold; }
        .section { margin-bottom: 50px; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }
    </style>
    """
    
    auth_spec = load_md("Auth_Flow_Spec.md")
    tdlib_spec = load_md("TDLib_JSON_Spec.md")
    opt_spec = load_md("Optimization_Strategy.md")
    
    html = f"""
    <html>
    <head><title>Internal System Documentation</title>{css}</head>
    <body>
        <h1>Escrow System - Internal Documentation</h1>
        <div class="nav">
            <a href="#auth">Auth Flow Specification</a>
            <a href="#tdlib">TDLib JSON Methods</a>
            <a href="#opt">Optimization Strategy</a>
            <a href="/docs">Public API (Swagger)</a>
        </div>
        
        <div id="auth" class="section">
            {auth_spec}
        </div>
        
        <div id="tdlib" class="section">
            {tdlib_spec}
        </div>
        
        <div id="opt" class="section">
            {opt_spec}
        </div>
    </body>
    </html>
    """
    return html
