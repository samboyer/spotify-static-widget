import ftplib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import dateutil.parser
import jinja2
import requests

# Settings
PLAYLIST_ID = Path("SPOTIFY_PLAYLIST_ID").read_text()
CLIENT_ID = Path("SPOTIFY_CLIENT_ID").read_text()
MAX_TRACKS = 5
FTP_SUBDIR = "fav-music"
# (order is preserved on widget!)
PLATFORMS_TO_LINK_TO = [
    "spotify",
    "youtube",
    "itunes",
    "deezer",
    "soundcloud",
]

PLATFORM_TO_ICON = {
    platform: f'<img src="assets/{platform}.svg" />'
    for platform in PLATFORMS_TO_LINK_TO
}

CLIENT_SECRET = open("SPOTIFY_CLIENT_SECRET").read().strip()
FTP_USER = open("FTP_USERNAME").read().strip()
FTP_DOMAIN = open("FTP_DOMAIN").read().strip()
FTP_PASS = open("FTP_PASSWORD").read().strip()
TRACKS_JSON_PATH = Path("tracks.json")


print("Getting API token from client secret...")
API_KEY = requests.post(
    "https://accounts.spotify.com/api/token",
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    },
).json()["access_token"]

# Fetch the playlist items
all_tracks = []
# List of data fields to get from the endpoint
api_fields = "items(track(id,artists(name),name, preview_url), added_at), next"
playlist_url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?{urlencode({'fields':api_fields,'limit':100})}"

more = True
while playlist_url:
    print(f"Fecthing {playlist_url}")
    data = requests.get(
        playlist_url, headers={"Authorization": f"Bearer {API_KEY}"}
    ).json()
    all_tracks.extend(data["items"])
    playlist_url = data["next"]

tracks_to_use = all_tracks[:5]


old_track_data = (
    json.loads(TRACKS_JSON_PATH.read_text()) if TRACKS_JSON_PATH.exists() else {}
)


# Fetch song.link data & artwork for each track

any_track_changed = False
Path("img").mkdir(exist_ok=True)
Path("audio").mkdir(exist_ok=True)

new_track_data = {}
for track in tracks_to_use:
    track_id = track["track"]["id"]
    # Don't fetch song.link data if we already have it
    if track_id in old_track_data:
        print(f"Already got '{track['track']['name']}'")
        new_track_data[track_id] = old_track_data[track_id]
    else:
        any_track_changed = True

        songlink_url = f"https://api.song.link/v1-alpha.1/links?url=spotify%3Atrack%3A{track_id}&userCountry=GB"
        print(f"Fecthing {songlink_url}...")
        songlink_data = requests.get(songlink_url).json()

        entity_data = songlink_data["entitiesByUniqueId"][
            [
                key
                for key in songlink_data["entitiesByUniqueId"].keys()
                if key.startswith("SPOTIFY_SONG::")
            ][0]
        ]

        thumbnail_url = entity_data["thumbnailUrl"]
        image_path = Path(f"img/{thumbnail_url.split('/')[-1]}.jpg")
        image_mono_path = image_path.with_suffix(".mono.png")

        print(f"Fetching {thumbnail_url}...")
        image_path.write_bytes(requests.get(thumbnail_url).content)

        preview_url = track["track"]["preview_url"]
        if preview_url is not None:
            preview_path = Path(f"audio/{preview_url.split('/')[-1].split('?')[0]}.mp3")
            print(f"Fetching {preview_url}...")
            preview_path.write_bytes(requests.get(preview_url).content)
            processed_preview_path = preview_path.with_suffix(".proc.mp3")
            print(f"Processing {preview_url}...")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    preview_path,
                    "-af",
                    "volume=0.25",
                    processed_preview_path,
                ]
            )

        print(f"Processing {image_path} with imagemagick...")
        subprocess.run(
            [
                "magick",
                "convert",
                image_path,
                "-colors",
                "2",
                "-resize",
                "200x200",
                "-ordered-dither",
                "o8x8,2",
                image_mono_path,
            ],
        )

        link_list = [
            {
                "platform": platform,
                "icon": PLATFORM_TO_ICON[platform],
                "url": songlink_data["linksByPlatform"][platform]["url"],
            }
            for platform in PLATFORMS_TO_LINK_TO
            if platform in songlink_data["linksByPlatform"]
        ]

        new_track_data[track_id] = {
            "title": entity_data["title"],
            "artist": entity_data["artistName"],
            "image": str(image_path),
            "image_mono": str(image_mono_path),
            "links": link_list,
            "songlink_url": songlink_data["pageUrl"],
            "preview_mp3": str(processed_preview_path) if preview_url else "",
        }

TRACKS_JSON_PATH.write_text(json.dumps(new_track_data, indent=2))


# Generate HTML
print("Generating HTML...")
environment = jinja2.Environment(loader=jinja2.FileSystemLoader("."))
results_template = environment.get_template("widget.html.tmpl")
with open("index.html", "w") as results:
    results.write(results_template.render({"tracks": list(new_track_data.values())}))

# Upload over FTP
# @@@ clear MP3 dir to prevent space buildup
dirs_to_clear = ["audio"]
files_to_upload = [
    "index.html",
    *(track["image_mono"] for track in new_track_data.values()),
    *(
        track["preview_mp3"]
        for track in new_track_data.values()
        if track["preview_mp3"]
    ),
    *Path("assets").glob("*"),
]


def _try_mkdir(ftp, dir):
    try:
        ftp.mkd(dir)
    except ftplib.error_perm as e:
        pass


print("Sending to FTP server...")
with ftplib.FTP(FTP_DOMAIN, FTP_USER, FTP_PASS) as ftp:
    ftp.cwd(FTP_SUBDIR)

    # @@@ empty audio dir so I don't eat all the space
    _try_mkdir(ftp, "img")
    _try_mkdir(ftp, "audio")
    _try_mkdir(ftp, "assets")

    for file in files_to_upload:
        with open(file, "rb") as f:
            print(f"Uploading {file}...")
            ftp.storbinary(f"STOR {file}", f)

    ftp.quit()
