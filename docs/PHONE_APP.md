# The phone app

An optional Android app that controls Jarvis on your PC from your phone: type or speak commands, answer
approvals, follow tasks, see the live status, and look at the PC's screen if you allow it. It doesn't run the
assistant itself. The PC does the work, and the phone is a remote.

## How it connects

The PC runs a small server, off until you turn it on. The phone connects to it over **Tailscale**, a free
private network between your own devices, so nothing on your PC is opened to the internet. Tailscale encrypts
the link. On top of that, every phone has to be **paired** with the PC once.

You can choose **Home network only** instead of Tailscale. Then the phone works only on the same Wi-Fi as the
PC, without Tailscale. Jarvis never listens on every network interface at once.

## Setting it up

1. Install [Tailscale](https://tailscale.com/download) on the PC and on the phone, and sign in to the same
   account on both.
2. Install the Jarvis app on the phone (see "Getting the app" below).
3. On the PC, open **Settings › Phone app**, tick **Phone app** and save.
4. Click **Pair a phone**. A QR code and a 6-digit code appear.
5. In the phone app, scan the QR code (or type the address and the code). The code works once, for 10 minutes,
   with 5 tries.

The phone keeps a token, and the PC stores only a fingerprint of it. **Remove** next to a phone in Settings
disconnects it at once and makes it pair again.

## Safety

- Every new pairing shows a **desktop notification**. If it wasn't you, remove the phone.
- An address that fails to connect or pair 5 times within a minute is **locked out for 5 minutes**.
- Approvals asked on the phone show the same details as on the PC. Their timer is longer
  (**Approval timer for phone commands**, 3 minutes by default) so you have time to pick up the phone.
- **Let the phone see the screen** decides whether the phone can request screenshots.
- Commands from the phone go through the same permission checks as anything else. See
  [SECURITY_MODEL.md](SECURITY_MODEL.md).

## Getting the app

There is no published APK yet; it will be attached to the releases. To build it yourself you need Node.js and
the Android SDK:

```
cd mobile
npm ci
npx tsc --noEmit && npx jest           # checks
npx expo run:android --variant release # builds and installs on a connected phone
```

The app's code is in `mobile/` (Expo and React Native, TypeScript). Its messages mirror the PC side, as
described in `mobile/src/api/protocol.ts` and `src/remote/phone_server.py`.

## When it doesn't connect

- Check that Tailscale is connected on both devices, with the same account.
- **Settings › Phone app** shows whether the server is listening, and on which address. **Restart server**
  restarts it.
- After 5 failed attempts the address is locked out for 5 minutes. Wait, then try again.
- If the phone was removed on the PC, pair it again.
