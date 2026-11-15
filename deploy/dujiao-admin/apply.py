"""Customise a Dujiao-Next source tree for GG Shop. Safe to run more than once.

usage: python3 apply.py /path/to/dujiao-next-src

- adds the 采购决策 menu and its six pages to the admin (they talk to dujiao-shim under /ops/api);
- removes the dashboard ad slots, the GitHub links and the unicorn icon, and renames the admin to GG Shop.
"""
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
        'fill="#0c6f5e"/><text x="32" y="43" font-family="Arial,sans-serif" font-size="30" font-weight="700" '
        'text-anchor="middle" fill="#fff">GG</text></svg>\n')

PAGES = [("decisions", "ProcurementDecisions", "待拍板", "ClipboardCheck"),
         ("pools", "ProcurementPools", "货源池", "Boxes"),
         ("channels", "ProcurementChannels", "货源渠道", "Link"),
         ("exceptions", "ProcurementExceptions", "异常与人工", "Bell"),
         ("log", "ProcurementLog", "采购记录", "ListOrdered"),
         ("codes", "ProcurementCodes", "自家卡密", "KeyRound")]
PERMISSION = "GET:/admin/site-connections"   # same right as managing upstream connections


def edit(path: Path, fn) -> None:
    old = path.read_text(encoding="utf-8")
    new = fn(old)
    if new != old:
        path.write_text(new, encoding="utf-8")
        print("patched", path)


def must(cond: bool, what: str) -> None:
    if not cond:
        sys.exit(f"apply.py: could not find {what}; the Dujiao source changed, update apply.py")


def router(s: str) -> str:
    if "procurement-decisions" in s:
        return s
    anchor = "      {\n        path: 'procurement-orders',"
    must(anchor in s, "the procurement-orders route")
    block = "".join(
        f"      {{\n        path: 'procurement/{slug}',\n        name: 'procurement-{slug}',\n"
        f"        component: () => import('@/views/admin/procurement/{comp}.vue'),\n"
        f"        meta: {{ permission: '{PERMISSION}' }},\n      }},\n" for slug, comp, _, _ in PAGES)
    return s.replace(anchor, block + anchor, 1)


def layout(s: str) -> str:
    if "id: 'procurement-decision'" not in s:
        anchor = "  const groups: NavGroup[] = [\n"
        must(anchor in s, "the nav groups")
        items = "".join(f"        {{\n          label: '{label}',\n          to: '/procurement/{slug}',\n"
                        f"          icon: {icon},\n          permission: '{PERMISSION}',\n        }},\n"
                        for slug, _, label, icon in PAGES)
        group = (f"    {{\n      id: 'procurement-decision',\n      label: '采购决策',\n      icon: ClipboardCheck,\n"
                 f"      items: [\n{items}      ],\n    }},\n")
        s = s.replace(anchor, anchor + group, 1)
    a = s.find('            <a\n              href="https://github.com/dujiao-next"')
    if a != -1:
        s = s[:a] + s[s.index("</a>\n", a) + 5:]
    return s.replace("© {{ new Date().getFullYear() }} Dujiao-Next ", "© {{ new Date().getFullYear() }} GG Shop ")


def dashboard(s: str) -> str:
    s = s.replace("import DashboardAd from '@/components/admin/DashboardAd.vue'\n", "")
    return re.sub(r"[ \t]*<DashboardAd [^>]*/>\n", "", s)


def i18n(s: str) -> str:
    s = s.replace("brand: 'Dujiao-Next Admin'", "brand: 'GG Shop 后台'")
    return re.sub(r"(controlRoom|workspace): 'Dujiao-Next Admin [^']*'", lambda m: f"{m.group(1)}: 'GG Shop 后台'", s)


def footer(s: str) -> str:
    a = s.find('          <p class="flex items-center justify-center gap-1 md:justify-start">\n            <a\n'
               '              href="https://github.com/dujiao-next"')
    if a != -1:
        s = s[:a] + s[s.index("</p>\n", a) + 5:]
    return s.replace("? siteName.trim() : 'Dujiao-Next'", "? siteName.trim() : 'GG Shop'")


def vault(s: str) -> str:
    s = re.sub(r'          <a href="https://github.com/dujiao-next".*?</a>\n', "", s, flags=re.S)
    return s.replace(" LogOut, Github,", " LogOut,")


def main(src: Path) -> None:
    admin, user = src / "frontend/admin", src / "frontend/user"
    must(admin.is_dir() and user.is_dir(), "frontend/admin and frontend/user")
    shutil.copy(HERE / "src/api/procurement.ts", admin / "src/api/procurement.ts")
    shutil.copytree(HERE / "src/views/admin/procurement", admin / "src/views/admin/procurement", dirs_exist_ok=True)
    edit(admin / "src/router/index.ts", router)
    edit(admin / "src/layouts/AdminLayout.vue", layout)
    edit(admin / "src/views/Dashboard.vue", dashboard)
    edit(admin / "src/i18n/index.ts", i18n)
    edit(admin / "index.html", lambda s: s.replace("<title>Dujiao-Next Admin</title>", "<title>GG Shop 后台</title>"))
    edit(user / "src/components/Footer.vue", footer)
    edit(user / "src/templates/vault/layout/VaultLayout.vue", vault)
    edit(user / "src/main.ts", lambda s: re.sub(r"brandLog\?\.\('%cGitHub[^\n]*\n", "", s))
    for d in (admin, user):
        (d / "public/dj.svg").write_text(ICON)
    print("done")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
