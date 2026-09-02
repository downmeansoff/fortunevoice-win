"""User-visible text, in English and Russian.

One flat catalogue rather than gettext: there are ~120 strings and two
languages, and a .po toolchain would add a build step, a binary artifact and a
compile-before-you-can-run rule to a project whose whole point is that you
clone it and run it.

`FVUILanguage` picks the language: `ru`, `en`, or `auto` (default), which
follows the Windows display language. It is deliberately separate from
`FVLanguage` — the language you *dictate* in has nothing to do with the
language you want the buttons in, and a Russian speaker dictating English
notes should not have the UI flip.

Rules for anything added here:

* Keep the placeholders identical between languages. `t()` formats with
  `str.format`, and a key missing a placeholder in one language raises there
  and nowhere else.
* Russian is not a transliteration of English. "Streak" is «серия», not
  «стрик»; "Insights" is «Статистика», because that is what the page shows.
* Missing keys fall back to English, then to the key itself. A missing
  translation must never be a crash in a dictation app.
"""

from __future__ import annotations

import locale
import re

from . import config
from .log import get as get_logger

logger = get_logger("strings")

SUPPORTED = ("en", "ru")

CATALOGUE: dict[str, dict[str, str]] = {
    # ── app-wide ────────────────────────────────────────────────────────
    "app.name": {"en": "FortuneVoice", "ru": "FortuneVoice"},
    "app.tagline": {"en": "Local dictation", "ru": "Локальная диктовка"},
    "app.runs_locally": {"en": "Runs fully on your PC",
                         "ru": "Работает целиком на вашем ПК"},

    # ── tray ────────────────────────────────────────────────────────────
    "tray.open": {"en": "Open FortuneVoice", "ru": "Открыть FortuneVoice"},
    "tray.setup": {"en": "Setup…", "ru": "Первый запуск…"},
    "tray.copy_last": {"en": "Copy last dictation",
                       "ru": "Копировать последнюю диктовку"},
    "tray.retype_last": {"en": "Type last dictation here",
                         "ru": "Напечатать последнюю сюда"},
    "tray.recover": {"en": "Recover failed dictation",
                     "ru": "Восстановить неудавшуюся диктовку"},
    "tray.retry_model": {"en": "Retry model load", "ru": "Загрузить модель заново"},
    "tray.language": {"en": "Speech language", "ru": "Язык речи"},
    "tray.microphone": {"en": "Microphone", "ru": "Микрофон"},
    "tray.quit": {"en": "Quit", "ru": "Выход"},
    "tray.title": {"en": "FortuneVoice — {label}", "ru": "FortuneVoice — {label}"},
    "tray.hold_to_dictate": {"en": "Hold {hotkey} to dictate",
                             "ru": "Удерживайте {hotkey} для диктовки"},

    # ── state names (tray tooltip) ──────────────────────────────────────
    "state.loading": {"en": "Loading the model…", "ru": "Загружаю модель…"},
    "state.idle": {"en": "Idle", "ru": "Готово"},
    "state.recording": {"en": "Recording", "ru": "Запись"},
    "state.processing": {"en": "Transcribing…", "ru": "Расшифровываю…"},
    "state.error": {"en": "Error", "ru": "Ошибка"},
    "state.model_failed": {"en": "Model failed to load",
                           "ru": "Модель не загрузилась"},

    # ── recording pill ──────────────────────────────────────────────────
    "pill.listening": {"en": "Listening", "ru": "Слушаю"},
    "pill.cleaning": {"en": "Cleaning up", "ru": "Вычитка"},
    "pill.transcribing": {"en": "Transcribing", "ru": "Расшифровка"},
    "pill.failed": {"en": "Failed", "ru": "Ошибка"},
    "pill.no_signal": {"en": "No mic signal", "ru": "Нет сигнала"},
    "pill.cancelled": {"en": "Cancelled", "ru": "Отменено"},

    # ── result panel ────────────────────────────────────────────────────
    "result.type_here": {"en": "Type it here", "ru": "Напечатать сюда"},
    "result.copy": {"en": "Copy", "ru": "Копировать"},
    "result.copied": {"en": "Copied", "ru": "Скопировано"},
    "result.failed": {"en": "Failed", "ru": "Не вышло"},
    "result.saved": {"en": "Saved to History", "ru": "Сохранено в историю"},

    # ── navigation ──────────────────────────────────────────────────────
    "nav.history": {"en": "History", "ru": "История"},
    "nav.insights": {"en": "Insights", "ru": "Статистика"},
    "nav.dictionary": {"en": "Dictionary", "ru": "Словарь"},
    "nav.settings": {"en": "Settings", "ru": "Настройки"},

    # ── history ─────────────────────────────────────────────────────────
    "history.search": {"en": "Search dictations", "ru": "Поиск по диктовкам"},
    "history.empty": {"en": "Nothing here yet.", "ru": "Пока пусто."},
    "history.no_matches": {"en": "No matches.", "ru": "Ничего не найдено."},
    "history.export_tip": {"en": "Export to a text file",
                           "ru": "Выгрузить в текстовый файл"},
    "history.delete_tip": {"en": "Delete all history", "ru": "Удалить всю историю"},
    "history.export_action": {"en": "Export", "ru": "Экспорт"},
    "history.delete_action": {"en": "Clear", "ru": "Очистить"},
    "history.deleted": {"en": "Deleted", "ru": "Удалено"},
    "history.undo": {"en": "Undo", "ru": "Вернуть"},
    "history.copied": {"en": "Copied", "ru": "Скопировано"},
    "history.export_empty": {"en": "Nothing to export",
                             "ru": "Выгружать нечего"},
    "history.export_empty_body": {
        "en": "History is empty — dictate something first.",
        "ru": "История пуста — сначала что-нибудь надиктуйте."},
    "history.export_failed": {"en": "Could not write the file",
                              "ru": "Не удалось записать файл"},
    "history.delete_title": {"en": "Delete all history", "ru": "Удалить всю историю"},
    "history.delete_body": {
        "en": "Permanently delete all {count} dictations?\n\nThis cannot be undone.",
        "ru": "Удалить все диктовки ({count})?\n\nОтменить это будет нельзя.",
    },
    "history.older_hidden": {
        "en": "{count} older dictations not shown — search to find them.",
        "ru": "Ещё {count} диктовок не показаны — найдите их поиском.",
    },
    "history.today": {"en": "Today", "ru": "Сегодня"},
    "history.yesterday": {"en": "Yesterday", "ru": "Вчера"},
    "history.earlier": {"en": "Earlier", "ru": "Раньше"},
    # Day and month names. `strftime("%A")` answers in the C locale — English,
    # whatever the UI language — so History showed «Сегодня», «Вчера», then
    # "Saturday". Russian months are genitive: the label reads "5 августа 2026".
    "date.weekday_0": {"en": "Monday", "ru": "Понедельник"},
    "date.weekday_1": {"en": "Tuesday", "ru": "Вторник"},
    "date.weekday_2": {"en": "Wednesday", "ru": "Среда"},
    "date.weekday_3": {"en": "Thursday", "ru": "Четверг"},
    "date.weekday_4": {"en": "Friday", "ru": "Пятница"},
    "date.weekday_5": {"en": "Saturday", "ru": "Суббота"},
    "date.weekday_6": {"en": "Sunday", "ru": "Воскресенье"},
    "date.month_1": {"en": "January", "ru": "января"},
    "date.month_2": {"en": "February", "ru": "февраля"},
    "date.month_3": {"en": "March", "ru": "марта"},
    "date.month_4": {"en": "April", "ru": "апреля"},
    "date.month_5": {"en": "May", "ru": "мая"},
    "date.month_6": {"en": "June", "ru": "июня"},
    "date.month_7": {"en": "July", "ru": "июля"},
    "date.month_8": {"en": "August", "ru": "августа"},
    "date.month_9": {"en": "September", "ru": "сентября"},
    "date.month_10": {"en": "October", "ru": "октября"},
    "date.month_11": {"en": "November", "ru": "ноября"},
    "date.month_12": {"en": "December", "ru": "декабря"},

    # ── insights ────────────────────────────────────────────────────────
    # Sentence case: the tiles used to shout these in all-caps, which at 8 px
    # turns Cyrillic into a grey smear.
    "insights.wpm": {"en": "Words per minute", "ru": "Слов в минуту"},
    "insights.total_words": {"en": "Total words", "ru": "Всего слов"},
    "insights.streak": {"en": "Days in a row", "ru": "Дней подряд"},
    "insights.last_30": {"en": "Last 30 days", "ru": "Последние 30 дней"},
    "insights.where": {"en": "Where you dictate", "ru": "Куда вы диктуете"},
    "insights.none_yet": {"en": "No dictations yet.", "ru": "Диктовок пока нет."},
    "insights.speed": {"en": "Speed", "ru": "Скорость"},
    # Units belong in the catalogue like everything else: a Russian window
    # reading "815 ms" is the one place the translation visibly stops.
    "unit.ms": {"en": "ms", "ru": "мс"},
    # The Whisper model list. The names are the models' own and stay as
    # they are; only what we say ABOUT them is translated.
    "model.fastest": {"en": "fastest, roughest", "ru": "быстрее всех, грубее всех"},
    "model.recommended": {"en": "recommended", "ru": "рекомендуется"},
    "model.most_accurate": {"en": "most accurate, slowest",
                            "ru": "точнее всех, медленнее всех"},
    "insights.median_total": {"en": "Median key-up to typed",
                              "ru": "Медиана: отпустил — напечаталось"},
    "insights.median_decode": {"en": "Median decode", "ru": "Медиана распознавания"},
    "insights.typed_share": {"en": "Typed straight into the app",
                             "ru": "Напечатано сразу в приложение"},

    # ── dictionary ──────────────────────────────────────────────────────
    "dict.intro": {
        "en": "Names and jargon Whisper keeps mishearing — one per line. Fed to "
              "the decoder as a prompt, and to the cleanup model as preferred "
              "spellings.",
        "ru": "Имена и термины, которые Whisper упорно слышит неверно — по одному "
              "в строке. Передаются распознавателю как подсказка, а модели "
              "вычитки — как предпочтительное написание.",
    },
    "dict.example": {"en": "Anthropic\nFortuneVoice\nqwen2.5\nCTranslate2",
                     "ru": "Фортуна\nЯндекс\nqwen2.5\nCTranslate2"},
    "dict.save": {"en": "Save", "ru": "Сохранить"},
    "dict.saved": {"en": "Saved {count} terms.", "ru": "Сохранено терминов: {count}."},
    "dict.saved_capped": {
        "en": "Saved {count} terms — only the first {chars} characters reach the model.",
        "ru": "Сохранено терминов: {count} — до модели дойдут только первые "
              "{chars} символов.",
    },

    # ── settings: groups ────────────────────────────────────────────────
    "settings.group_dictation": {"en": "Dictation", "ru": "Диктовка"},
    "settings.group_text": {"en": "Text processing", "ru": "Обработка текста"},
    "settings.group_general": {"en": "General", "ru": "Общее"},

    # ── settings: rows ──────────────────────────────────────────────────
    "settings.activation": {"en": "Activation", "ru": "Режим"},
    "settings.activation_hint": {"en": "hold = push to talk",
                                 "ru": "удержание — говорить, пока держите"},
    "settings.hold": {"en": "Hold to talk", "ru": "Удерживать"},
    "settings.toggle": {"en": "Tap to toggle", "ru": "Нажатием вкл/выкл"},
    "settings.shortcut": {"en": "Shortcut", "ru": "Сочетание клавиш"},
    "settings.shortcut_hint": {"en": "click, then press the keys you want",
                               "ru": "нажмите, затем — нужные клавиши"},
    # Ctrl and Alt are allowed as the shortcut itself, but they keep doing
    # their normal job, so a short tap has to be ignored — otherwise Ctrl+C
    # would start a dictation. Say so, or holding looks like it is broken.
    "settings.shortcut_modifier_hint": {
        "en": "Ctrl / Alt work too — hold them briefly to start",
        "ru": "Ctrl и Alt тоже подходят — их нужно чуть придержать"},
    "settings.shortcut_press": {"en": "Press keys — Esc cancels",
                                "ru": "Нажмите клавиши — Esc отменит"},
    "settings.shortcut_bad_title": {"en": "That shortcut will not work",
                                    "ru": "Такое сочетание не сработает"},
    "settings.language": {"en": "Speech language", "ru": "Язык речи"},
    "settings.lang_ru": {"en": "Russian", "ru": "Русский"},
    "settings.lang_en": {"en": "English", "ru": "Английский"},
    "settings.lang_auto": {"en": "Auto-detect", "ru": "Определять"},
    "settings.microphone": {"en": "Microphone", "ru": "Микрофон"},
    "settings.mic_default": {"en": "System default", "ru": "Как в системе"},
    "settings.streaming": {"en": "Streaming transcription",
                           "ru": "Распознавать по ходу речи"},
    "settings.streaming_hint": {"en": "decodes while you speak",
                                "ru": "расшифровка идёт, пока вы говорите"},
    "settings.cleanup": {"en": "AI cleanup (Ollama)", "ru": "Вычитка ИИ (Ollama)"},
    "settings.cleanup_hint": {"en": "removes filler words and fixes punctuation",
                              "ru": "убирает слова-паразиты и правит пунктуацию"},
    "settings.cleanup_model": {"en": "Cleanup model", "ru": "Модель вычитки"},
    "settings.cleanup_model_hint": {
        "en": "qwen2.5:3b — the smallest one that does not translate your text",
        "ru": "qwen2.5:3b — меньшие переводят текст вместо вычитки"},
    # The memory-versus-latency trade. Worth surfacing rather than deciding
    # for the user: on a small card holding the model makes the whole desktop
    # stutter, on a large one letting it go is 3.5 s wasted.
    "settings.keep_alive": {"en": "Keep the model in memory",
                            "ru": "Держать модель в памяти"},
    "settings.keep_alive_hint": {
        "en": "how long it stays after the last dictation — shorter frees "
              "~2 GB, and costs ~3 s next time",
        "ru": "сколько держать после последней диктовки: меньше — освободится "
              "~2 ГБ видеопамяти, но +3 с к следующей"},
    "settings.keep_alive_never": {"en": "Unload at once", "ru": "Выгружать сразу"},
    "settings.keep_alive_5m": {"en": "5 minutes", "ru": "5 минут"},
    "settings.keep_alive_1h": {"en": "1 hour", "ru": "1 час"},
    "settings.keep_alive_always": {"en": "Always", "ru": "Всегда"},
    "settings.voice_commands": {"en": "Line breaks by voice",
                                "ru": "Переносы строк голосом"},
    "settings.voice_commands_hint": {
        "en": "say «new line» or «new paragraph» as a sentence of its own",
        "ru": "скажите «новая строка» или «новый абзац» отдельной фразой"},
    "settings.smartfix": {"en": "Auto-fix garbled words",
                          "ru": "Чинить искажённые слова"},
    "settings.smartfix_hint": {"en": "only on low-confidence transcripts",
                               "ru": "только когда распознавание не уверено"},
    "settings.cleanup_note": {
        "en": "Cleanup rewrites your words with a local model. The raw transcript "
              "is always kept in History next to the cleaned one.",
        "ru": "Вычитка переписывает ваши слова локальной моделью. Исходный текст "
              "всегда лежит в истории рядом с обработанным.",
    },
    "settings.launch_failed": {
        "en": "Windows would not let FortuneVoice write to the Startup folder:\n{folder}\n\nYou can put a shortcut there by hand.",
        "ru": "Windows не дал записать ярлык в папку автозагрузки:\n{folder}\n\nМожно положить ярлык туда вручную."},
    "settings.launch_at_login": {"en": "Launch at login", "ru": "Запускать при входе"},
    "settings.sounds": {"en": "Sound feedback", "ru": "Звуковые сигналы"},
    "settings.overlay": {"en": "Recording overlay", "ru": "Плашка записи"},
    "settings.overlay_hint": {"en": "the floating pill while you speak",
                              "ru": "плавающая полоска, пока вы говорите"},
    "settings.whisper_model": {"en": "Whisper model", "ru": "Модель распознавания"},
    "settings.whisper_model_hint": {"en": "bigger is more accurate and slower",
                                    "ru": "крупнее — точнее и медленнее"},
    "settings.clipboard": {"en": "Paste via clipboard", "ru": "Вставлять через буфер"},
    "settings.clipboard_hint": {"en": "only for apps that ignore typed input",
                                "ru": "для программ, игнорирующих набор с клавиатуры"},
    "settings.ui_language": {"en": "Interface language", "ru": "Язык интерфейса"},
    "settings.ui_language_hint": {"en": "restart to apply everywhere",
                                  "ru": "перезапустите, чтобы применить везде"},
    "settings.ui_auto": {"en": "Match Windows", "ru": "Как в Windows"},
    "settings.data_location": {"en": "Data and logs live in {path}",
                               "ru": "Данные и логи лежат в {path}"},
    "settings.open_folder": {"en": "Open folder", "ru": "Открыть папку"},
    "settings.restart_needed": {"en": "Restart to apply",
                                "ru": "Нужен перезапуск"},

    # ── settings: ollama status ─────────────────────────────────────────
    "ollama.checking": {"en": "checking Ollama…", "ru": "проверяю Ollama…"},
    "ollama.off": {"en": "Cleanup is off.", "ru": "Вычитка выключена."},
    "ollama.not_running": {
        "en": "Ollama is not running — dictations are saved and typed raw.",
        "ru": "Ollama не запущена — текст сохраняется и печатается без вычитки.",
    },
    "ollama.model_missing": {"en": "Ollama is running, but {model} is not pulled.",
                             "ru": "Ollama работает, но модель {model} не скачана."},
    "ollama.ready": {"en": "Ollama ready: {model}.", "ru": "Ollama готова: {model}."},

    # ── delivery outcomes (the result panel's reason line) ──────────────
    "hold.stale": {"en": "Took a while — saved to History",
                   "ru": "Долго считалось — сохранено в историю"},
    "hold.focus": {"en": "You switched windows — saved to History",
                   "ru": "Вы сменили окно — сохранено в историю"},
    "hold.noedit": {"en": "No text field — saved to History",
                    "ru": "Нет текстового поля — сохранено в историю"},
    "hold.failed": {"en": "Couldn't type it — saved to History",
                    "ru": "Не удалось напечатать — сохранено в историю"},

    # ── notifications ───────────────────────────────────────────────────
    "notify.bad_hotkey": {"en": "Bad hotkey", "ru": "Неверное сочетание клавиш"},
    "notify.hotkey_dead": {"en": "The shortcut stopped working",
                           "ru": "Сочетание клавиш перестало работать"},
    "notify.hotkey_dead_body": {
        "en": "Windows refused the keyboard hook for {hotkey}. Restart FortuneVoice.",
        "ru": "Windows отказал в перехвате {hotkey}. Перезапустите FortuneVoice."},
    "dict.unsaved": {"en": "Unsaved changes", "ru": "Есть несохранённое"},
    "notify.lost": {"en": "FortuneVoice couldn’t make out that dictation",
                    "ru": "Не удалось разобрать эту диктовку"},
    "notify.stuck": {"en": "That dictation got stuck",
                     "ru": "Диктовка застряла"},
    "notify.stuck_body": {
        "en": "The audio was saved — use “Recover failed dictation” in the tray.",
        "ru": "Звук сохранён — «Восстановить неудавшуюся диктовку» в трее."},
    "notify.history_failed": {"en": "This dictation was not saved",
                              "ru": "Эта диктовка не сохранилась"},
    "notify.history_failed_body": {
        "en": "The text was typed, but History could not be written — check the disk.",
        "ru": "Текст напечатан, но записать историю не удалось — проверьте диск."},
    "notify.ui_failed": {"en": "A window failed to open",
                         "ru": "Окно не открылось"},
    "notify.no_cleanup": {"en": "Dictation works, cleanup does not",
                          "ru": "Диктовка работает, вычитка — нет"},
    "notify.ollama_missing": {
        "en": "Ollama is not installed. Text will be typed exactly as heard.",
        "ru": "Ollama не установлена. Текст будет напечатан как расслышан."},
    "notify.ollama_down": {
        "en": "Ollama would not start. Text will be typed exactly as heard.",
        "ru": "Ollama не запустилась. Текст будет напечатан как расслышан."},
    "notify.model_failed": {"en": "FortuneVoice couldn't load the model",
                            "ru": "FortuneVoice не смог загрузить модель"},
    "notify.no_audio": {"en": "FortuneVoice can't hear you",
                        "ru": "FortuneVoice вас не слышит"},
    "notify.recovered": {"en": "Recovered — saved to History",
                         "ru": "Восстановлено — сохранено в историю"},

    # ── onboarding ──────────────────────────────────────────────────────
    "setup.title": {"en": "FortuneVoice — setup", "ru": "FortuneVoice — настройка"},
    "setup.heading": {"en": "Ready to dictate", "ru": "Готово к диктовке"},
    "setup.privacy": {"en": "Everything runs on this machine. Audio never leaves it.",
                      "ru": "Всё работает на этом компьютере. Звук никуда не уходит."},
    "setup.hold_to_talk": {"en": "Hold to talk", "ru": "Удерживайте"},
    "setup.how": {"en": "Hold it, speak, let go. The text is typed where your cursor is.",
                  "ru": "Зажмите, скажите, отпустите. Текст напечатается там, где курсор."},
    "setup.microphone": {"en": "Microphone", "ru": "Микрофон"},
    "setup.say_something": {"en": "Say something…", "ru": "Скажите что-нибудь…"},
    "setup.hearing_you": {"en": "Hearing you clearly.", "ru": "Слышу вас чётко."},
    "setup.no_microphone": {"en": "No microphone: {error}",
                            "ru": "Микрофон недоступен: {error}"},
    "setup.model": {"en": "Model", "ru": "Модель"},
    "setup.model_ready": {"en": "{model} on {device} — ready",
                          "ru": "{model} на {device} — готова"},
    "setup.model_loading": {
        "en": "downloading and loading… the first dictation will wait for it",
        "ru": "скачивается и загружается… первая диктовка её дождётся",
    },
    "setup.start": {"en": "Start dictating", "ru": "Начать диктовать"},
    "setup.tray_hint": {"en": "The tray icon has settings, history and this screen again.",
                        "ru": "Значок в трее откроет настройки, историю и этот экран."},
}


def _system_language() -> str:
    """Windows display language, reduced to a code we have strings for."""
    try:
        import ctypes

        # GetUserDefaultUILanguage returns an LCID; the low 10 bits are the
        # primary language. 0x19 is Russian.
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lcid & 0x3FF) == 0x19:
            return "ru"
        return "en"
    except Exception:  # noqa: BLE001 - not Windows, or a locked-down process
        code = (locale.getdefaultlocale()[0] or "")[:2].lower()
        return code if code in SUPPORTED else "en"


def language() -> str:
    """The language to render the UI in."""
    choice = (config.get_str("FVUILanguage") or "auto").lower()
    if choice in SUPPORTED:
        return choice
    return _system_language()


def t(key: str, **fmt) -> str:
    """Translate `key`, formatting with `fmt`.

    Falls back to English, then to the key itself: a missing translation must
    show something imperfect, never raise, in an app whose windows appear over
    the user's work.
    """
    entry = CATALOGUE.get(key)
    if entry is None:
        logger.debug("no string for %r", key)
        return key
    text = entry.get(language()) or entry.get("en") or key
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError) as exc:
        logger.warning("bad placeholders in %r (%s)", key, exc)
        return re.sub(r"\{[^}]*\}", "", text).strip()
