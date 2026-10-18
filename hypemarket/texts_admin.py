"""Strings of the bot-admin panel (/admin) and the required-channel gate. Merged into texts.FA / texts.EN."""

FA_ADMIN = {
    # --- what a player sees when the gate is on and they have not joined
    "gate.need": "🔒 <b>{name}</b>، برای استفاده از ربات اول باید عضو کانال <b>{channel}</b> بشی.\n\nعضو شو و بعد دکمه‌ی «✅ عضو شدم» رو بزن.",
    "gate.join": "📢 عضویت در کانال",
    "gate.joined": "✅ عضو شدم",
    "gate.cb": "🔒 اول باید عضو کانال {channel} بشی؛ بعد دوباره امتحان کن.",
    "gate.ok": "✅ عضویت تأیید شد، خوش اومدی! حالا می‌تونی از ربات استفاده کنی.",
    "gate.still": "هنوز عضو کانال {channel} نیستی. اول عضو شو، بعد دوباره بزن.",
    # --- the panel
    "ad.main": "⚙️ <b>پنل ادمین</b>\n\n📢 کانال اجباری: {chan}\n💾 پشتیبان‌گیری: هر {every} • آخرین: {last}\n\nاین پنل فقط برای ادمین‌های فایل .env بازه.",
    "ad.b.chan": "📢 کانال اجباری", "ad.b.backup": "💾 پشتیبان‌گیری", "ad.b.main": "🔙 پنل ادمین",
    "ad.b.set": "✏️ تنظیم کانال", "ad.b.on": "🔛 روشن کردن قفل", "ad.b.off": "⏸ خاموش کردن قفل", "ad.b.check": "🔍 بررسی دسترسی",
    "ad.b.rm": "🗑 حذف کانال", "ad.b.rmok": "✅ بله، حذف کن", "ad.b.cancel": "❌ انصراف", "ad.b.bknow": "📤 همین الان بفرست",
    "ad.chan_none": "تنظیم نشده", "ad.on": "روشن ✅", "ad.off": "خاموش ⏸", "ad.never": "هنوز نه", "ad.nolink": "—",
    "ad.chan": ("📢 <b>کانال اجباری</b>\n\nکانال: {chan}\nقفل عضویت: {state}\nلینک عضویت: {link}\n\n"
                "وقتی قفل روشنه، هر بازیکنی (به‌جز ادمین‌ها) که عضو این کانال نباشه از ربات جواب نمی‌گیره و بهش گفته می‌شه اول عضو بشه.\n\n{hint}"),
    "ad.hint_none": "برای شروع: ربات رو ادمین کانالت کن، بعد «تنظیم کانال» رو بزن.",
    "ad.hint_set": "اگه ربات از ادمینی کانال حذف بشه، بازیکن‌ها بدون قفل ادامه می‌دن و بهت خبر می‌دم.",
    "ad.ask": ("✏️ آیدی کانال رو بفرست؛ مثل <code>@mychannel</code> یا <code>t.me/mychannel</code>.\n"
               "برای کانال خصوصی، شماره‌ی آیدیش رو بفرست (مثل <code>-1001234567890</code>).\n\n⚠️ ربات باید ادمین اون کانال باشه."),
    "ad.set_ok": "✅ کانال <b>{chan}</b> تنظیم شد و قفل عضویت <b>روشن</b> شد. از الان بازیکن‌ها باید عضو بشن.",
    "ad.err.bad_ref": "❌ این آیدی کانال معتبر نیست. مثلاً <code>@mychannel</code> بفرست.",
    "ad.err.not_found": "❌ کانالی با این آیدی پیدا نکردم. درست نوشتی؟",
    "ad.err.not_channel": "❌ این یه کانال (یا سوپرگروه) نیست.",
    "ad.err.bot_not_admin": "❌ ربات ادمین این کانال نیست. اول ربات رو ادمین کانال کن و دوباره آیدی رو بفرست.",
    "ad.err.api": "❌ تلگرام جواب نداد؛ چند لحظه بعد دوباره امتحان کن.",
    "ad.retry": "آیدی دیگه‌ای بفرست یا «❌ انصراف» رو بزن.",
    "ad.need_chan": "اول یه کانال تنظیم کن.",
    "ad.turned_on": "🔛 قفل عضویت روشن شد.", "ad.turned_off": "⏸ قفل عضویت خاموش شد؛ بازیکن‌ها آزادانه ادامه می‌دن.",
    "ad.check_ok": "✅ همه‌چیز درسته: ربات ادمین {chan} هست و عضویت بازیکن‌ها قابل بررسیه.",
    "ad.rm_ask": "🗑 کانال {chan} حذف بشه؟ بعدش دیگه از بازیکن‌ها خواسته نمی‌شه عضو بشن.",
    "ad.removed": "🗑 کانال حذف شد و قفل عضویت خاموشه.",
    "ad.backup": ("💾 <b>پشتیبان‌گیری</b>\n\nهر {every} یه نسخه‌ی کامل و بررسی‌شده‌ی دیتابیس برای همه‌ی ادمین‌ها می‌فرستم (بی‌صدا).\n"
                  "آخرین: {last}\nنسخه‌ی بعدی: {next}\nدریافت‌کننده‌ها: {n} ادمین"),
    "ad.now": "همین الان (سر وقت بعدی)",
    "ad.bk_done": "✅ پشتیبان برای {n} ادمین فرستاده شد.",
    "ad.private_only": "⚙️ پنل ادمین فقط توی چت خصوصی با ربات باز می‌شه. بیا پی‌وی.",
    "ad.denied": "⛔",
}

EN_ADMIN = {
    "gate.need": "🔒 <b>{name}</b>, to use the bot you first need to join <b>{channel}</b>.\n\nJoin, then tap “✅ I joined”.",
    "gate.join": "📢 Join the channel",
    "gate.joined": "✅ I joined",
    "gate.cb": "🔒 Join {channel} first, then try again.",
    "gate.ok": "✅ Membership confirmed, welcome! You can use the bot now.",
    "gate.still": "You haven't joined {channel} yet. Join first, then tap again.",
    "ad.main": "⚙️ <b>Admin panel</b>\n\n📢 Required channel: {chan}\n💾 Backups: every {every} • last: {last}\n\nThis panel is only for the admins listed in the .env file.",
    "ad.b.chan": "📢 Required channel", "ad.b.backup": "💾 Backups", "ad.b.main": "🔙 Admin panel",
    "ad.b.set": "✏️ Set channel", "ad.b.on": "🔛 Turn the lock on", "ad.b.off": "⏸ Turn the lock off", "ad.b.check": "🔍 Check access",
    "ad.b.rm": "🗑 Remove channel", "ad.b.rmok": "✅ Yes, remove it", "ad.b.cancel": "❌ Cancel", "ad.b.bknow": "📤 Send one now",
    "ad.chan_none": "not set", "ad.on": "on ✅", "ad.off": "off ⏸", "ad.never": "not yet", "ad.nolink": "—",
    "ad.chan": ("📢 <b>Required channel</b>\n\nChannel: {chan}\nMembership lock: {state}\nJoin link: {link}\n\n"
                "While the lock is on, every player (except admins) who is not a member of this channel gets no answer from the bot and is told to join first.\n\n{hint}"),
    "ad.hint_none": "To start: make the bot an administrator of your channel, then tap “Set channel”.",
    "ad.hint_set": "If the bot loses its admin rights in the channel, players carry on without the lock and I'll tell you.",
    "ad.ask": ("✏️ Send the channel's username, like <code>@mychannel</code> or <code>t.me/mychannel</code>.\n"
               "For a private channel send its numeric id (like <code>-1001234567890</code>).\n\n⚠️ The bot must be an administrator of that channel."),
    "ad.set_ok": "✅ Channel <b>{chan}</b> is set and the membership lock is <b>on</b>. From now on players must join.",
    "ad.err.bad_ref": "❌ That is not a valid channel name. Send something like <code>@mychannel</code>.",
    "ad.err.not_found": "❌ I couldn't find a channel with that name. Is it spelled right?",
    "ad.err.not_channel": "❌ That is not a channel (or supergroup).",
    "ad.err.bot_not_admin": "❌ The bot is not an administrator of that channel. Make it one first, then send the name again.",
    "ad.err.api": "❌ Telegram did not answer; try again in a moment.",
    "ad.retry": "Send another name or tap “❌ Cancel”.",
    "ad.need_chan": "Set a channel first.",
    "ad.turned_on": "🔛 Membership lock is on.", "ad.turned_off": "⏸ Membership lock is off; players carry on freely.",
    "ad.check_ok": "✅ All good: the bot is an administrator of {chan} and can check players' membership.",
    "ad.rm_ask": "🗑 Remove the channel {chan}? Players will no longer be asked to join.",
    "ad.removed": "🗑 Channel removed; the membership lock is off.",
    "ad.backup": ("💾 <b>Backups</b>\n\nEvery {every} I send every admin a complete, verified copy of the database (silently).\n"
                  "Last: {last}\nNext: {next}\nRecipients: {n} admin(s)"),
    "ad.now": "right away (at the next check)",
    "ad.bk_done": "✅ Backup sent to {n} admin(s).",
    "ad.private_only": "⚙️ The admin panel only opens in a private chat with the bot. Come to my DMs.",
    "ad.denied": "⛔",
}
