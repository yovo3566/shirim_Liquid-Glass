import yt_dlp
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI()

# מאפשר ל-Flutter Web לפנות לשרת ללא חסימות CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/search")
def search(q: str):
    if not q:
        return []
    ydl_opts = {
        'default_search': 'ytsearch10',
        'quiet': True,
        'extract_flat': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch10:{q}", download=False)
            results = []
            for entry in info.get('entries', []):
                results.append({
                    'id': entry.get('id', ''),
                    'title': entry.get('title', 'ללא כותרת'),
                    'artist': entry.get('uploader', 'אמן לא ידוע'),
                    'thumb': f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg"
                })
            return results
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/stream/{video_id}")
def stream(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            audio_url = info.get('url')
        except Exception as e:
            raise HTTPException(status_code=404, detail="השיר לא נמצא")

    def generate():
        res = requests.get(audio_url, stream=True)
        for chunk in res.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    return StreamingResponse(generate(), media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)