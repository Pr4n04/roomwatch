# RoomWatch

Point the webcam on your door. When someone walks in, your phone buzzes with a
photo and a name — **"Alice is in your room"** — and when they leave, you get told
that too.

Runs entirely on your own laptop. No cloud, no subscription, no account. The only
thing that ever leaves the machine is the single annotated snapshot attached to an
alert.

Alerts go to your phone over **ntfy** — install an app, subscribe to a topic,
done. No account, no token, no sign-up, nothing to renew. Your topic is already
configured. If you would rather not use ntfy, a **webhook** (Discord, or anything
that accepts a multipart POST) works just as well.

Built for Windows 11 on an HP laptop, but the Python is cross-platform.

---

## Read this part first: the one real limitation

**RoomWatch only works while the laptop is awake, logged in, and the lid is
open.** It is a program on a laptop, not a camera in the cloud.

So it means:

- Plugged in, on a stand or a shelf, **lid open, screen off**. That is fine —
  Windows happily keeps running with the display off.
- **Do not close the lid.** By default Windows sleeps when you do, and RoomWatch
  stops. `keep_awake.bat` changes that (and tells you how to undo it).
- If you take the laptop out, RoomWatch is not running. Nothing watches an
  unplugged, shut laptop.
- It must be **plugged in** all day. On battery a gaming laptop will sleep on
  battery-saver long before the day is out.
- Windows must not update and reboot mid-lecture. Checkpoint your work.

If you need something that works when the laptop is shut, you want a real
security camera or a mains-powered Pi. This is the version you can build this
afternoon.

**Worth telling the people you live with** that there is a camera in the room.
In the UK, recording other people in a shared home without them knowing is
genuinely dicey even if you own the camera. Two sentences in the group chat
avoids the whole problem.

---

## 1. Install

1. Install **Python 3.11 or 3.12** from [python.org/downloads](https://www.python.org/downloads/).
   Tick **"Add python.exe to PATH"** on the first screen. If you already have
   Python 3.9–3.13, skip this.
2. Unzip RoomWatch somewhere permanent and obvious, like `C:\RoomWatch`.
   Not OneDrive, not Desktop, not a folder you'll tidy away.
3. Double-click **`setup.bat`**.

It creates a `venv` folder, installs two packages, downloads the two face models
(~40 MB), and stops. If it fails, the error is usually the Python PATH tick or a
network that blocks pypi.org — the script tells you which.

Then set up your phone (section 3) and prove the link works by double-clicking
**`test_phone.bat`**.

### You never need to type a path

Everything below has a double-clickable `.bat` sitting next to it, and each one
changes to the right folder for you:

| Double-click | What it does |
|--------------|--------------|
| `setup.bat` | one-time install (venv + models) |
| `test_phone.bat` | send a test photo to your phone, prove the link works |
| `check_camera.bat` | confirm the watcher can actually read the camera |
| `enroll.bat` | asks who to add, then captures photos of them |
| `start_roomwatch.bat` | start watching, with a window you can read |
| `start_hidden.bat` | start watching with no window |
| `install_autostart.bat` | start it automatically at every logon |

If you would rather use the keyboard, every command is
`venv\Scripts\python.exe <script>` run from inside the `C:\RoomWatch` folder.

## 2. Teach it who lives with you

This is the step that decides whether names come out right. Do not skip it.

**Close the Windows Camera app, Teams and Zoom first** — whichever grabs the
webcam first keeps it, and RoomWatch will just fail to open it.

Point the laptop at yourself and run, once per person:

```
venv\Scripts\python.exe enroll.py "Alice"
```

A window opens. **Space** captures one photo, **r** clears what you've captured
so far, **q** quits. Aim for **15–25 samples each**, and vary what you do between
shots:

- head roughly centred, filling the middle third of the frame
- looking straight on, then ~30°, then ~45°
- a couple of metres back, then closer
- with and without glasses; different hair; a hat on one or two
- **once with the window behind you and once in a dim room** — the door end of a
  room is usually badly lit, and that is exactly where your camera will be

Better light, better results. Do not enrol from a photo of the person on a
screen; it does not work.

Then check it:

```
venv\Scripts\python.exe enroll.py --list
```

You want **12+ samples per person**. Below 6 and names will be unreliable.

Already have photos on your phone? Skip the webcam and point it at a folder —
names come from the filenames, so `Alice_01.jpg` → Alice:

```
venv\Scripts\python.exe enroll.py --from C:\Users\You\Pictures\alice
```

To remove someone: `enroll.py --remove "Alice"`

## 3. Get the alerts onto your phone

Alerts go out over **ntfy** — no account, no token, no sign-up. You need a topic
name, and you need the same name in two places: your phone, and `config.json`.

1. Install the **ntfy** app on your phone — [Play Store](https://play.google.com/store/apps/details?id=de.marmaro.krt.ffup) or
   [App Store](https://apps.apple.com/app/ntfy/id1400395123).
2. Open it, tap **+**, and subscribe to a topic. Make it long and random, like
   `room-k3f9x2mq7dp1` — the "Secret"/"Secured" button in some versions of the
   app just invents a name for you, so any long random name is exactly as good.
3. Put that same name in `config.json`:

```json
"ntfy": {
  "enabled": true,
  "server": "https://ntfy.sh",
  "topic": "room-k3f9x2mq7dp1"
}
```

There is no `config.json` in a fresh clone on purpose: yours holds a secret, and
git history never forgets one. `setup.bat` copies the tracked
`config.example.json` to `config.json` on first run, and `.gitignore` keeps your
real one out of every commit.

4. On iOS, allow notifications when it asks. On Android, allow ntfy to download
   attachments and disable battery optimisation for it, or photos may be delayed
   or never fetched.

That is the whole setup. Double-click **`test_phone.bat`** to prove it: it sends
one photo and one line of text, and prints `sent` or `FAILED` for the channel.
If you skip setting a topic, it generates a random one for you.

### Read this before you point it at the room

On the public `ntfy.sh` server, **anyone who knows a topic name can read that
topic, and there is no authentication.** Message IDs are sequential, so a
stranger who guessed your topic could page back through the history and look at
every alert photo you have sent.

Photos are attached to ntfy, not stored in it, and ntfy.sh **deletes every
attachment after 3 hours** (the alert text stays for 12). So an alert photo is
not a permanent record — but for those 3 hours it sits at a plain public URL,
and message IDs are sequential, so anyone who guessed your topic could page back
through the history and look at it.

That is why the topic name has to be unguessable. A name like `roomwatch`, or
anything containing your name, hands your alert photos to a stranger who typed
three guesses. If you would rather not think about it, self-host ntfy and point
`server` at your own machine, so the photos never touch a public relay at all.

Changing it later is one line in `config.json`:

```json
"ntfy": {
  "enabled": true,
  "server": "https://ntfy.sh",
  "topic": "room-k3f9x2mq7dp1"
}
```

Then re-subscribe in the app to the new name.

### Optional — a webhook instead

If you would rather not use a public relay at all, a webhook sends a standard
multipart POST to a URL you control: a `file` part holding the JPEG and a
`content` part holding the caption. In Discord: Server Settings → Integrations →
Webhooks → New Webhook → Copy URL, then:

```json
"webhook": {
  "enabled": true,
  "url": "https://discord.com/api/webhooks/123456/abc..."
}
```

Delete the Discord webhook once you have pasted the URL in, so the URL itself is
the only key. Slack is the exception: it wants a publicly reachable image URL
rather than an upload, so ntfy suits Slack better.

It sends one photo and one line of text to every channel you have enabled, and
prints `sent` or `FAILED` for each. If nothing is set up yet it tells you exactly
what is missing and walks you through ntfy. This script needs no camera and no
enrolled faces, so it is the fastest way to know the phone side is alive.

## 4. Turn it on

Check the wiring first — models, camera, and whether a phone channel is live:

```
venv\Scripts\python.exe roomwatch.py --check
```

Then just run it:

```
venv\Scripts\python.exe roomwatch.py
```

or double-click `start_roomwatch.bat`, which keeps a window open so you can read
the log. Ctrl+C stops it.

You should see something like:

```
known faces: 2 ({'Alice': 21, 'Bob': 18})
phone channels: Ntfy
RoomWatch started | source=camera 0 | 5.0 fps | alerts on
```

**Test it before you rely on it.** Have someone walk in who is not enrolled — you
want "Someone is in your room" plus a photo within a second or two. Then walk in
yourself and check the name is right. Get this working at your desk before
pointing it at the door.

### Pausing the alerts

There is no chat interface any more — ntfy and webhooks are send-only, because
there is no phone app that will hold a conversation with your laptop. Pausing is
two double-clickable files instead:

| Double-click | What happens |
|--------------|--------------|
| `pause.bat`  | Creates a file called `paused`. RoomWatch keeps watching, but stops sending. |
| `resume.bat` | Deletes it. Alerts go back to normal. |

The take effect on the next event, so a photo already on its way still arrives.
If RoomWatch was started *before* you paused, it stays quiet until you resume —
that is the `--pause` flag doing its job, and the log line
`alerts muted` will tell you which one happened.

## 5. Keeping it running

**Start at every logon:**

```
install_autostart.bat
```

Creates a scheduled task. It runs with no window, and logs to `roomwatch.log`.
To stop it right now: `schtasks /end /tn "RoomWatch"`. To remove it:
`uninstall_autostart.bat`.

**Stop Windows sleeping** (Administrator command prompt):

```
keep_awake.bat
```

Sleep off, hibernation off, lid-close does nothing. Read the warning in that
file first — **it means the laptop will not sleep in a bag either.** Undo with
`undo_keep_awake.bat`.

## 6. Settings worth touching

Everything is in `config.json` with a `_comment` next to each block. The ones
that actually change day-to-day life:

| Setting | Default | What it does |
|---------|---------|--------------|
| `alerts.heartbeat_minutes` | `0` (off) | `30` = a photo every 30 min, even if nobody moves. Good reassurance. |
| `alerts.unknown_cooldown_seconds` | `300` | Ignore repeat alerts for the same unknown face for 5 min |
| `alerts.night_mode` | off | `enabled: true` = only alert on **unknown** people between 23:00 and 07:00. Housemates stop lighting up your phone. |
| `alerts.send_on_person_leave` | `true` | Turn off if you only care about arrivals |
| `alerts.resend_while_present_seconds` | `0` (off) | `900` = re-alert every 15 min while someone is still there |
| `camera.target_fps` | `5` | Inference costs ~15–30 ms/frame, so 5 is smooth and cheap. `10` if you want tighter timing. |
| `detection.recognition_threshold` | `0.363` | Lower = more people identified, more chance of misidentifying. Raise to `0.45` if it names the wrong person. |
| `storage.keep_last_n` | `200` | Snapshots kept on disk |
| `runtime.log_level` | `INFO` | `DEBUG` if something is odd |

Photos are annotated with each person's name and match confidence, so a snapshot
doubles as a way to spot a bad identification before it becomes a false alarm.

## If it isn't working

**"Could not open camera index 0"** — the most common one.
- Close the Windows Camera app, Teams, Zoom, and any browser tab using the
  camera. Only one program can hold it.
- Settings → Privacy & security → Camera: make sure it's on and "Let apps
  access your camera" is on.
- Some HP lids have a **physical camera-shutter slider** next to the webcam.
  Check it is open.
- Try `--camera 1`, then `--camera 2`.

**It detects people but always says "Unknown"** — not enough enrolment, or bad
light. Run `enroll.py --list` and add samples. Test the *actual* spot: have
someone stand where the door is, in the real lighting, and run
`enroll.py "Name"` pointed at that spot.

**It names the wrong person** — raise `detection.recognition_threshold` to
`0.42`, and add more varied samples for both people.

**No notifications arrive** — run `test_phone.py` first; it tells you which
channel is missing or failing and why. If it says `No phone channel configured`,
something is still a placeholder or the JSON is malformed. If it says `FAILED`,
run it again and read `roomwatch.log` for the reason. For ntfy, check the topic
name in `config.json` matches your subscription in the app character for
character — that is the usual cause.

**ntfy delivers but the photo is missing** — check three things in order.

1. Did it actually upload? Open the topic's raw feed in a browser:
   `https://ntfy.sh/<your topic>/json` — every line is a message, and a photo
   shows an `attachment` object with its own `url`. If there is no
   `attachment`, the upload failed; `roomwatch.log` says why.
2. The topic name in `config.json` must match your subscription exactly.
3. The ntfy app must be allowed to fetch attachments (Android: it downloads
   the image itself, so allow notifications and exempt it from battery
   optimisation; iOS: allow notifications and check Settings → ntfy → Photos).

Note the 📷 emoji on every message. That is ntfy rendering the `camera` tag
that RoomWatch sends, not the photograph — it appears on plain text alerts
too. The photo is a separate message, one above the caption.

**Nothing at all in the log for 30s then it stops** — check `roomwatch.log`. If
it ends at a "reopening it" message, the camera keeps dropping out; lower
`camera.frame_width`/`frame_height` to `640`/`480`.

**Verify the whole thing works** — `python selftest.py` builds a synthetic room
video with three faces in it, enrols two, runs the real pipeline against local
mock ntfy and webhook servers, and checks the alerts that come out (including
that each photo is a real decodable JPEG on every channel). It never touches your
own enrolled faces, and it never posts to your real ntfy topic. 73 checks on a
fresh clone, 76 once your own config is filled in, about
50 seconds.

## What is where

```
config.example.json    all settings, as a template (tracked)
config.json            your settings -- gitignored, it holds your ntfy topic
roomwatch.py           the watcher
enroll.py              add / list / remove people
test_phone.py          send one test alert to your phone
test_phone.bat         send a test alert to your phone
pause.bat              mute alerts (keeps watching)
resume.bat             unmute alerts
check_camera.bat      confirm the camera can be read
enroll.bat             add a person, asks who
selftest.py            73-check self-test, no camera needed
fetch_models.py        download the two ONNX models
faceengine.py          face detection + recognition
facedb.py              the enrolled-faces database
presence.py            tracking, who's here, when to alert
notifiers.py           ntfy, webhook, console
render.py              the annotated photo
faces/people.json      your enrolled faces  (BACK THIS UP)
models/                the two ONNX models
snapshots/             alert photos on disk
roomwatch.log          the log
```

`faces/people.json` is the only irreplaceable file in there. Copy it somewhere
safe and you can rebuild the environment from scratch in ten minutes.

## How it works, briefly

A face detector (YuNet) finds faces in each frame, a 128-number embedding
(SFace) describes each one, and that embedding is compared against your enrolled
faces by cosine similarity. Above the threshold it's a name; below it, `Unknown`.

Both models ship inside `opencv-python` and run on plain CPU, so there is no
PyTorch, no CUDA, and nothing to compile. Face boxes are stitched into
persistent tracks, a track's name is decided by a quality-weighted vote rather
than a single frame, and alerts fire on *transitions* — arrival and departure —
not on every frame where a face is visible. That is why you get one buzz when
someone walks in rather than forty.

## Accuracy, honestly

Good at: telling known people apart in reasonable light, catching anyone at all
wearing a face, and not crying wolf.

Bad at: telling identical twins apart, someone in a balaclava, or a face lit
only from a window behind them. It will say `Unknown` rather than guess — that's
deliberate, and the photo is there so you can judge for yourself.

It is a room-occupancy notifier, not a security system. Treat it as "somebody
walked in" rather than evidence of anything.
