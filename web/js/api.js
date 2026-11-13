// Thin wrapper over the local Shirim HTTP API.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const options = { method };
  if (method === 'POST') {
    // The server rejects state-changing requests without this header (blocks cross-site requests)
    options.headers = { 'Content-Type': 'application/json', 'X-Shirim': '1' };
    options.body = JSON.stringify(body ?? {});
  }
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch {
    // non-JSON error pages
  }
  if (!response.ok) throw new ApiError(response.status, data?.error || `HTTP ${response.status}`);
  return data;
}

export const api = {
  get: (path, params) => request('GET', params ? `${path}?${new URLSearchParams(params)}` : path),
  post: (path, body) => request('POST', path, body),
};

// Present only inside the Android app
export const native = window.ShirimAndroid || null;

const FRIENDLY_ERRORS = [
  [/not a bot|Sign in to confirm/i, 'יוטיוב חוסם זמנית את החיבור. אפשר להתחבר לחשבון יוטיוב בהגדרות או לנסות מאוחר יותר.'],
  [/Video unavailable|not available/i, 'השיר לא זמין ביוטיוב (הוסר או חסום באזור).'],
  [/Private video/i, 'הסרטון פרטי.'],
  [/age|inappropriate/i, 'הסרטון מוגבל לגיל ודורש התחברות לחשבון יוטיוב.'],
  [/Failed to fetch|NetworkError|Load failed/i, 'אין חיבור לאפליקציה או לאינטרנט.'],
  [/HTTP Error 403/i, 'יוטיוב דחה את ההורדה. נסה שוב.'],
  [/Not enough space on the USB stick/i, 'אין מספיק מקום בדיסק און קי.'],
  [/USB stick is no longer connected/i, 'הדיסק און קי כבר לא מחובר. חבר אותו ונסה שוב.'],
  [/Copying is already in progress/i, 'כבר מתבצעת העתקה לדיסק און קי.'],
  [/Wrong code/i, 'הקוד שגוי. בדקו את הקוד שמופיע עכשיו במחשב.'],
  [/No pairing code is active/i, 'הקוד פג תוקף. לחצו שוב על "חיבור טלפון" במחשב.'],
  [/Computer unreachable|The computer is off/i, 'המחשב לא נמצא. ודאו שהאפליקציה פתוחה במחשב ושהטלפון מחובר לאותו WiFi.'],
  [/only on the computer/i, 'השיר נמצא רק במחשב. הוא יועתק כשתהיו בבית, באותו WiFi.'],
  [/Copying from the computer failed/i, 'ההעתקה מהמחשב נכשלה. נסו שוב כשתהיו בבית.'],
  [/Not paired/i, 'הטלפון לא מחובר למחשב.'],
  [/Invalid computer address/i, 'כתובת המחשב לא תקינה.'],
  [/Spotify page could not be read|Spotify playlist not found/i, 'לא הצלחנו לקרוא את הפלייליסט מספוטיפיי. ודאו שהוא ציבורי ושהקישור נכון.'],
  [/None of the songs were found/i, 'לא נמצא אף אחד מהשירים ביוטיוב.'],
  [/No songs found in the text/i, 'לא נמצאו שירים בטקסט. כתבו שיר בכל שורה, למשל: עומר אדם - תל אביב'],
  [/Nothing to import/i, 'הדביקו קישור או רשימת שירים.'],
  [/Hidden in filtered mode/i, 'התוכן הזה מוסתר במצב מסונן.'],
  [/Wrong filter code/i, 'קוד הסינון שגוי.'],
  [/filtered edition cannot/i, 'בגרסה המסוננת אי אפשר לשנות את הסינון.'],
  [/Filter code must be/i, 'קוד הסינון צריך להיות 4 עד 8 ספרות.'],
  [/No update available/i, 'אין גרסה חדשה במחשב.'],
  [/update was not copied completely/i, 'העדכון לא הועתק עד הסוף. נסו שוב.'],
  [/format is not available/i, 'הפורמט הזה לא זמין במכשיר הזה.'],
  [/Cannot use folder/i, 'אי אפשר לשמור שירים בתיקייה הזאת.'],
];

export function friendlyError(error) {
  const text = error?.message || String(error || '');
  for (const [pattern, message] of FRIENDLY_ERRORS) {
    if (pattern.test(text)) return message;
  }
  return text || 'שגיאה לא ידועה';
}
