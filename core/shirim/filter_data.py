"""Known performers and given names for filtered mode (content_filter.py).

Names are matched after content_filter.artist_key(): lower case, single spaces. Wikidata covers the rest.
"""

# Women singing, and groups with a woman singing
FEMALE_ARTISTS = {
    # Israel
    'נועה קירל', 'עדן בן זקן', 'אודיה', 'שרית חדד', 'ריטה', 'עופרה חזה', 'זהבה בן', 'מירי מסיקה', 'אנה זק',
    'אגם בוחבוט', 'נסרין קדרי', 'קרן פלס', 'מאיה בוסקילה', 'שירי מימון', 'נטע ברזילי', 'נטע', 'יובל דיין',
    'נגה ארז', 'אילנית', 'ירדנה ארזי', 'חוה אלברשטיין', 'חווה אלברשטיין', 'נורית גלרון', 'רבקה זהר',
    'קורין אלאל', 'אחינועם ניני', 'נועה', 'מירי בן ארי', 'רוני דלומי', 'גלי עטרי', 'שלומית אהרון', 'ריקי גל',
    'יהודית רביץ', 'מזי כהן', 'אסתר עופרים', 'רינת גבאי', 'חני נחמיאס', 'איה כורם', 'אלה לי להב', 'דיקלה',
    'יעל נעים', 'דנה אינטרנשיונל', 'אסתר רדא', 'נינט טייב', 'נינט', 'רונה קינן', 'עמית פרקש', 'אתי אנקרי',
    'מאיה בלזיצמן', 'רונית שחר', 'עדי כהן', 'אביגיל רוז', 'חנה לסלאו', 'שושנה דמארי', 'ג\'ודי שטרן',
    'noa kirel', 'eden ben zaken', 'odeya', 'sarit hadad', 'rita', 'ofra haza', 'zehava ben', 'miri mesika',
    'anna zak', 'agam buhbut', 'nasrin kadri', 'keren peles', 'maya buskila', 'shiri maimon', 'netta',
    'netta barzilai', 'yuval dayan', 'noga erez', 'ilanit', 'yardena arazi', 'chava alberstein', 'nurit galron',
    'achinoam nini', 'miri ben-ari', 'roni dalumi', 'gali atari', 'riki gal', 'yehudit ravitz', 'esther ofarim',
    'rinat gabay', 'hani nahmias', 'ayah korem', 'dikla', 'yael naim', 'dana international', 'esther rada',
    'ninet tayeb', 'ninet', 'rona kenan', 'amit farkash', 'etti ankri', 'shoshana damari',
    # International
    'taylor swift', 'adele', 'billie eilish', 'dua lipa', 'rihanna', 'beyoncé', 'beyonce', 'ariana grande',
    'shakira', 'lady gaga', 'katy perry', 'selena gomez', 'olivia rodrigo', 'sia', 'miley cyrus', 'doja cat',
    'nicki minaj', 'cardi b', 'sza', 'lana del rey', 'sabrina carpenter', 'tate mcrae', 'camila cabello',
    'halsey', 'ava max', 'karol g', 'rosalía', 'rosalia', 'madonna', 'mariah carey', 'whitney houston',
    'celine dion', 'céline dion', 'britney spears', 'jennifer lopez', 'alicia keys', 'christina aguilera',
    'p!nk', 'pink', 'lorde', 'ellie goulding', 'zara larsson', 'anne-marie', 'bebe rexha', 'charli xcx',
    'jessie j', 'demi lovato', 'kesha', 'fergie', 'nelly furtado', 'avril lavigne', 'norah jones',
    'amy winehouse', 'tina turner', 'cher', 'dolly parton', 'aretha franklin', 'gloria gaynor', 'kylie minogue',
    'raye', 'chappell roan', 'gracie abrams', 'ice spice', 'megan thee stallion', 'normani', 'tinashe',
    'anitta', 'becky g', 'natti natasha', 'inna', 'alexandra stan', 'loreen', 'måneskin',
    'blackpink', 'twice', 'newjeans', 'aespa', 'itzy', 'le sserafim', 'little mix', 'fifth harmony',
    'spice girls', "destiny's child", 'the pussycat dolls', 'pussycat dolls', 't.a.t.u.', 'sugababes',
    'girls aloud', 'the saturdays', 'tlc', 'en vogue', 'bananarama',
    'abba', 'fleetwood mac', 'no doubt', 'paramore', 'evanescence', 'the cranberries', 'roxette', 'eurythmics',
    'the corrs', 'florence + the machine', 'florence and the machine', 'clean bandit', 'the white stripes',
    'arcade fire', 'the xx', 'echosmith', 'carpenters', 'the carpenters', 'boney m.', 'ace of base', 'aqua',
    'la bouche', 'vengaboys', 'the black eyed peas', 'black eyed peas', 'within temptation', 'nightwish',
    'the pretty reckless', 'lovejoy', 'marina and the diamonds', 'marina', 'years & years',
}

# Performed by men only; saves a lookup for the most played Israeli and Jewish music
MALE_ARTISTS = {
    'עומר אדם', 'אייל גולן', 'ישי ריבו', 'אושר כהן', 'עדן חסון', 'חנן בן ארי', 'שולי רנד', 'פאר טסי', 'מרגי',
    'סטטיק ובן אל', 'סטטיק ובן אל תבורי', 'בן צור', 'איתי לוי', 'רביד פלוטניק', 'אביב גפן', 'שלמה ארצי',
    'אריק איינשטיין', 'שלום חנוך', 'יהורם גאון', 'זוהר ארגוב', 'חיים משה', 'משה פרץ', 'דודו אהרון',
    'ליאור נרקיס', 'הראל סקעת', 'עידן רייכל', 'ישי לוי', 'קובי פרץ', 'שלומי שבת', 'אמיר דדון', 'יונתן רזאל',
    'אברהם פריד', 'מרדכי בן דוד', 'בני פרידמן', 'מוטי שטיינמץ', 'אהרן רזאל', 'עקיבא', 'חיים ישראל',
    'יעקב שוואקי', 'עוזיה צדוק', 'דקלון', 'בועז שרעבי', 'אביתר בנאי', 'מאיר בנאי', 'אהוד בנאי', 'ברי סחרוף',
    'טונה', 'סאבלימינל', 'הצל', 'נתן גושן', 'אליעד', 'עידן עמדי', 'שלמה גרוניך', 'מתי כספי', 'גידי גוב',
    'יהודה פוליקר', 'רמי קלינשטיין', 'דני סנדרסון', 'ששון שאולוב', 'ששון איפרם שאולוב', 'אבי ביטר',
    'ליאור פרחי', 'יוסי בנאי', 'אלון עדר', 'נתן גושן', 'שמעון בוסקילה', 'חיים אוליאל', 'ג\'קי מקייטן',
    'מושיק עפיה', 'אבי אבורומי', 'שי גבסו', 'נתנאל דוד', 'איציק דדיה', 'יוסף שטרית', 'בניה ברבי', 'רון נשר',
    'omer adam', 'eyal golan', 'ishay ribo', 'osher cohen', 'eden hason', 'hanan ben ari', 'shuli rand',
    'peer tasi', 'margi', 'static & ben el', 'static & ben el tavori', 'ben zur', 'itay levi', 'ravid plotnik',
    'aviv geffen', 'shlomo artzi', 'arik einstein', 'moshe peretz', 'dudu aharon', 'lior narkis', 'harel skaat',
    'idan raichel', 'kobi peretz', 'shlomi shabat', 'amir dadon', 'yonatan razel', 'avraham fried',
    'mordechai ben david', 'benny friedman', 'motty steinmetz', 'yaakov shwekey', 'akiva', 'chaim israel',
    'subliminal', 'hatikva 6', 'hadag nahash', 'mashina', 'infected mushroom', 'ethnix', 'tislam',
}

# Given names (first word of a performer's name) that are clearly female or clearly male.
# Names used for both (עדן, גל, טל, שי, עדי, רוני, יובל, אופק, ליאור, מאור...) are deliberately left out.
FEMALE_FIRST_NAMES = {
    'agam', 'anna', 'dana', 'daniela', 'dikla', 'efrat', 'ella', 'emily', 'emma', 'esther', 'hila', 'hodaya',
    'jasmin', 'jennifer', 'jessica', 'kate', 'katy', 'keren', 'lady', 'laura', 'lihi', 'linda', 'lisa', 'mary',
    'maya', 'michal', 'miri', 'miss', 'moran', 'naama', 'nasrin', 'netta', 'nina', 'noa', 'noga', 'ofra', 'olivia',
    'orit', 'ortal', 'rachel', 'reut', 'sarah', 'sarit', 'shira', 'shiri', 'sophie', 'tamar', 'yael', 'yasmin',
    'אביבית', 'אביגיל', 'אביטל', 'אדוה', 'אודיה', 'אורטל', 'אורין', 'אורית', 'אורנה', 'אושרת', 'איה', 'איילת',
    'אילנית', 'אלה', 'אלינור', 'אנאל', 'אנה', 'אסנת', 'אסתר', 'אפרת', 'אתי', 'בת', 'גאיה', 'גלי', 'גלית', 'דנה',
    'דניאלה', 'דפנה', 'דקלה', 'הדס', 'הודיה', 'הילה', 'זהבה', 'זיוה', 'חוה', 'חווה', 'חנה', 'חני', 'טליה', 'יהודית',
    'יסמין', 'יעל', 'יערה', 'יפית', 'ירדנה', 'כנרת', 'לאה', 'ליאת', 'ליבי', 'ליה', 'ליהי', 'לימור', 'מאי', 'מאיה',
    'מורן', 'מזי', 'מיכל', 'מירב', 'מירי', 'מרגלית', 'מרים', 'נגה', 'נויה', 'נועה', 'נורית', 'נחמה', 'נטע', 'נינט',
    'נסרין', 'נעמה', 'סיגל', 'עופרה', 'עינב', 'עלמה', 'ענבל', 'ענבר', 'פזית', 'צופית', 'קרן', 'רבקה', 'רונית',
    'רחל', 'ריטה', 'רינת', 'ריקי', 'רעות', 'שושנה', 'שירה', 'שירי', 'שלהבת', 'שלומית', 'שמרית', 'שני', 'שרית',
    'תהילה', 'תמר', 'תמרה',
}
MALE_FIRST_NAMES = {
    'aharon', 'akiva', 'amir', 'amitai', 'amos', 'arik', 'arkadi', 'asaf', 'assaf', 'avi', 'avishai', 'avraham',
    'avrumi', 'barak', 'baruch', 'ben', 'benjamin', 'benny', 'beri', 'binyamin', 'boaz', 'bob', 'bruno', 'chaim',
    'chris', 'daniel', 'david', 'dudi', 'dudu', 'ehud', 'elad', 'eldad', 'eli', 'elvis', 'eran', 'erez', 'eviatar',
    'eyal', 'frank', 'george', 'guy', 'hagai', 'haim', 'hanan', 'harel', 'harry', 'hilik', 'idan', 'ido', 'ishay',
    'itamar', 'itay', 'itzik', 'james', 'john', 'justin', 'kobi', 'lipa', 'mark', 'matan', 'meir', 'mendy',
    'michael', 'mordechai', 'mordy', 'moshe', 'moti', 'motty', 'nachman', 'nadav', 'naftali', 'natan', 'nathan',
    'nissim', 'ofer', 'ohad', 'omer', 'omri', 'paul', 'peter', 'rafael', 'rami', 'ravid', 'refael', 'reuven',
    'robert', 'roy', 'shalom', 'shimon', 'shloime', 'shlomi', 'shlomo', 'shmuel', 'shmulik', 'shulem', 'steve',
    'tom', 'tomer', 'tzvika', 'udi', 'uri', 'yaakov', 'yair', 'yehoshua', 'yehuda', 'yishai', 'yisrael', 'yitzchak',
    'yitzhak', 'yoav', 'yonatan', 'yoni', 'yosef', 'yossi', 'zvika', 'אבי', 'אביחי', 'אבירם', 'אבישי', 'אביתר',
    'אבנר', 'אברהם', 'אברימי', 'אהוד', 'אהרון', 'אהרן', 'אודי', 'אוהד', 'אורי', 'אוריאל', 'אייל', 'איציק', 'איתי',
    'איתמר', 'איתן', 'אלדד', 'אלון', 'אלחנן', 'אלי', 'אליאב', 'אליהו', 'אליעד', 'אליעזר', 'אלירן', 'אלעד', 'אמיר',
    'אמיתי', 'אסי', 'אסף', 'ארז', 'ארי', 'אריה', 'ארנון', 'ארקדי', 'בועז', 'ביני', 'בן', 'בני', 'בניה', 'בנימין',
    'בנצי', 'ברוך', 'ברק', 'גבי', 'גדי', 'גיא', 'גלעד', 'דביר', 'דוד', 'דודו', 'דודי', 'דן', 'דני', 'דניאל', 'הלל',
    'הראל', 'זאב', 'חגי', 'חזי', 'חיים', 'חיימי', 'חיליק', 'חנוך', 'חנן', 'טוביה', 'יאיר', 'יאנקי', 'יגאל', 'יהודה',
    'יהונתן', 'יהורם', 'יהושע', 'יואב', 'יוני', 'יונתן', 'יוסי', 'יוסל', 'יוסף', 'יחיאל', 'ינון', 'יניב', 'יענקי',
    'יעקב', 'יצחק', 'ירון', 'ישי', 'ישעיה', 'ישעיהו', 'ישראל', 'כפיר', 'לוי', 'לייבי', 'מאיר', 'מוטי', 'מוישה',
    'מוישי', 'מושיקו', 'מיכאל', 'מנחם', 'מני', 'מענדי', 'מרדכי', 'משה', 'מתי', 'מתן', 'נדב', 'נחום', 'נחמן',
    'ניסים', 'ניר', 'נסים', 'נפתלי', 'נתן', 'נתנאל', 'סמי', 'עוזי', 'עוזיה', 'עומר', 'עופר', 'עזרא', 'עידו', 'עידן',
    'עמוס', 'עמיר', 'עמרי', 'עקיבא', 'ערן', 'פיני', 'פנחס', 'צבי', 'צביקה', 'צחי', 'קובי', 'ראובן', 'רביב', 'רביד',
    'רון', 'רועי', 'רמי', 'רפאל', 'רפי', 'שאול', 'שגיא', 'שולם', 'שייקה', 'שימי', 'שלום', 'שלומי', 'שלמה', 'שמואל',
    'שמוליק', 'שמחה', 'שמילי', 'שמעון', 'שמשון', 'ששון', 'תומר',
}
