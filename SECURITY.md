> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-05-30<br>
> https://maduinos.blogspot.com/

# Security Policy

This sketch sends keyboard input to the connected host. Treat unexpected HID behavior as a safety issue.

Macro recording is global while the red recording indicator is active. On Linux/Wayland, enabling
recording grants the account read access to `/dev/input`, which includes all keyboards and can expose
passwords to any process running as that account. macroKey opens those devices only for an explicit
recording and tries to remove password-shaped steps, but that filter is not a security boundary.

Stored profiles, their previous-version backup, and the rotating diagnostic log may contain captured
text. The app creates them owner-only (mode `0600` on systems that support Unix permissions); exported
profiles should be handled as sensitive data too.

## Reporting

If you find unsafe keyboard behavior or private data exposure, contact Maduinos through:

<https://biz.maduinos.com/>
