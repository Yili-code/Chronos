"""Human-facing reset replies; operational diagnostics stay in saved state."""
from html import escape

from .ai import AIError
from .google_calendar import CalendarError


class ResetInProgress(Exception):
    """A duplicate delivery must wait for its original reset receipt."""


def category(value='study'):
    return {'study':'讀書','meeting':'會議','work':'工作','workout':'運動',
            'social':'社交','exam':'考試','contest':'競賽','other':'其他活動'}.get(value,value)


def question(error):
    if isinstance(error, AIError) or str(error).startswith(('The AI service', 'The configured AI model')):
        return '這次沒能完成重排，安排服務未能處理這個要求。請稍後再試。'
    if isinstance(error, CalendarError):
        return '我目前無法確認日曆上的行程。請稍後再試。'
    return str(error)


def unchanged(error):
    return escape(question(error)) + '\n\n你的行程沒有變動。'


def partial(state):
    label=escape(category(state.get('event_type','study')))
    retry='重排所有'+category(state.get('event_type','study'))+'行程'
    return (f'{label}行程重排暫停了，還沒有全部完成。\n\n'
            f'目前已移除 {len(state.get("deleted",[]))} 個舊行程，建立 {len(state.get("inserted",[]))} 個新時段。\n\n'
            f'請傳送「{escape(retry)}」，我會先核對已完成的部分，再接續處理。')


BUSY = ('目前有另一項排程操作尚待確認，我先不啟動這次重排。\n\n'
        '你的行程沒有因這次要求而變動。請稍後再試。')
