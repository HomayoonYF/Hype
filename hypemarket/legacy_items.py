"""The 17 starter items of v0.21, kept ONLY as data for migration 14.

v0.211 removes every seeded item from the catalog (nothing is registered or granted by default). A database that already holds one of these items (or a gift code that names one) keeps
its ownership: the migration turns the definition of each item that is referenced anywhere into a `legacy` row of `item_templates`, so it is still shown by name. Nothing here is registered in
`items.ITEMS` and no code grants these items.
"""
# (id, name_fa, name_en, category, slot, max_stack, icon, description_fa, description_en)
LEGACY = (
    ('tshirt_white', 'تی\u200cشرت سفید', 'White T-shirt', 'clothing', 'upper', 1, '👕', 'تی\u200cشرت نخی ساده\u200cی سفید برای هر روز.', 'A plain white cotton T-shirt for every day.'),
    ('hoodie_green', 'هودی سبز', 'Green hoodie', 'clothing', 'upper', 1, '🧥', 'هودی گرم و سبز تیره با کلاه.', 'A warm dark green hoodie with a hood.'),
    ('jeans_blue', 'شلوار جین آبی', 'Blue jeans', 'clothing', 'lower', 1, '👖', 'شلوار جین آبی و راحت.', 'Comfortable blue jeans.'),
    ('chinos_beige', 'شلوار کتان کرم', 'Beige chinos', 'clothing', 'lower', 1, '👖', 'شلوار کتان کرم\u200cرنگ، مناسب جاهای رسمی\u200cتر.', 'Beige cotton trousers for more formal places.'),
    ('cap_black', 'کلاه لبه\u200cدار مشکی', 'Black cap', 'clothing', 'headwear', 1, '🧢', 'کلاه لبه\u200cدار مشکی.', 'A black baseball cap.'),
    ('sneakers_white', 'کفش ورزشی سفید', 'White sneakers', 'footwear', 'footwear', 1, '👟', 'کفش ورزشی سفید و سبک.', 'Light white sneakers.'),
    ('boots_brown', 'بوت قهوه\u200cای', 'Brown boots', 'footwear', 'footwear', 1, '🥾', 'بوت چرمی قهوه\u200cای.', 'Brown leather boots.'),
    ('sunglasses_black', 'عینک آفتابی', 'Sunglasses', 'accessories', 'accessory', 1, '🕶', 'عینک آفتابی با فریم مشکی.', 'Sunglasses with a black frame.'),
    ('watch_steel', 'ساعت مچی فلزی', 'Steel wristwatch', 'accessories', 'accessory', 1, '⌚', 'ساعت مچی با بند فلزی.', 'A wristwatch with a steel strap.'),
    ('sandwich', 'ساندویچ', 'Sandwich', 'food', None, 20, '🥪', 'یه ساندویچ آماده.', 'A ready-made sandwich.'),
    ('soda_can', 'نوشابه', 'Soda', 'food', None, 24, '🥤', 'یه قوطی نوشابه\u200cی خنک.', 'A can of cold soda.'),
    ('notebook', 'دفترچه\u200cی یادداشت', 'Notebook', 'personal', None, 1, '📓', 'دفترچه\u200cای برای نوشتن چیزهایی که نباید یادت بره.', 'A notebook for things you should not forget.'),
    ('umbrella', 'چتر', 'Umbrella', 'personal', None, 1, '☂️', 'یه چتر تاشو.', 'A folding umbrella.'),
    ('gift_box', 'جعبه\u200cی هدیه', 'Gift box', 'gifts', None, 10, '🎁', 'یه جعبه\u200cی هدیه\u200cی بسته\u200cبندی\u200cشده.', 'A wrapped gift box.'),
    ('greeting_card', 'کارت تبریک', 'Greeting card', 'gifts', None, 50, '💌', 'کارت تبریک با یه پیام دست\u200cنویس.', 'A greeting card with a handwritten note.'),
    ('old_key', 'کلید قدیمی', 'Old key', 'misc', None, 1, '🗝', 'یه کلید قدیمی؛ معلوم نیست قفل کجاست.', 'An old key; nobody knows which lock it opens.'),
    ('matchbox', 'جعبه\u200cی کبریت', 'Matchbox', 'misc', None, 20, '🔥', 'یه جعبه کبریت.', 'A box of matches.'),
)
