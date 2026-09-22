"""Validate and present DOM conversation candidates, never assert reply IDs."""
import re


def context_posts(post):
    items = post.get('context', [])
    if not isinstance(items, list) or not str(post.get('id', '')).isdigit():
        return []
    accepted = []
    seen = set()
    for item in items[-3:]:
        if not isinstance(item, dict):
            continue
        ident, author, text = str(item.get('id', '')), item.get('username'), item.get('text')
        ordered = item.get('relation') == 'preceding_in_thread' and item.get('evidence') == 'detail_order'
        linked = (item.get('relation') == 'parent_reported_by_feed' and item.get('evidence') == 'codex_reset_parent_link'
                  and post.get('discovery_source') == 'https://codex-reset.com/'
                  and str(item.get('reply_id')) == str(post['id']) and ident == post.get('parent_id')
                  and isinstance(author, str) and author.lower() == str(post.get('parent_username', '')).lower())
        if (not ident.isdigit() or int(ident) >= int(post['id']) or ident in seen
                or not isinstance(author, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', author)
                or not isinstance(text, str) or not text.strip() or len(text) > 25000
                or item.get('complete') is not True or not (ordered or linked)):
            continue
        seen.add(ident)
        accepted.append({'id':ident, 'username':author, 'text':text,
                         'url':f'https://x.com/{author}/status/{ident}',
                         'relation':item['relation'], 'evidence':item['evidence'], 'complete':True,
                         **({'reply_id':str(post['id'])} if linked else {})})
    return accepted


def context_text(items):
    return '\n\n'.join(item['text'] for item in items)


def context_display(items):
    if not items:
        return ''
    return ('\n\n对话上文候选（关系来自网页位置或第三方父帖链接，未核实直接回复关系；用户观点不等于官方承诺）：\n'
            + '\n\n'.join(f"@{item['username']}\n{item['url']}\n{item['text']}" for item in items))
