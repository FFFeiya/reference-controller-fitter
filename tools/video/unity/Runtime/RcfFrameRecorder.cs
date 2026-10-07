// Demo helper: records the game deterministically. Time.captureFramerate makes game time advance
// exactly 1/fps per rendered frame regardless of how slow the capture is; physics runs at 1/60 s.
// Each frame the camera is rendered into a 1920x1080 texture and saved as PNG; a CSV logs the
// input and player state for the on-screen key display in the final video.
using System;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

public class RcfFrameRecorder : MonoBehaviour
{
    public int fps = 30;
    public float duration = 13f;
    public int width = 1920, height = 1080;
    string dir, signal;
    RenderTexture rt;
    Texture2D tex;
    Camera cam;
    int frame;
    readonly StringBuilder csv = new StringBuilder("frame,t,axis,jump,x,y\n");
    RcfDemoInput input;
    Transform player;
    float[] shotTimes = { 4.4f, 6.2f, 8.2f };
    int shotIdx;

    void Awake()
    {
        Time.fixedDeltaTime = 1f / 60f;
        Time.captureFramerate = fps;
        dir = Environment.GetEnvironmentVariable("RCF_FRAMES");
        signal = Environment.GetEnvironmentVariable("RCF_SIGNAL");
        string d = Environment.GetEnvironmentVariable("RCF_DURATION");
        if (!string.IsNullOrEmpty(d)) duration = float.Parse(d, CultureInfo.InvariantCulture);
        Directory.CreateDirectory(dir);
        cam = GetComponent<Camera>();
        rt = new RenderTexture(width, height, 24);
        tex = new Texture2D(width, height, TextureFormat.RGB24, false);
    }

    void Start()
    {
        input = GameObject.Find("Player").GetComponent<RcfDemoInput>();
        player = GameObject.Find("Player").transform;
    }

    void LateUpdate()
    {
        var prev = cam.targetTexture;
        cam.targetTexture = rt;
        cam.Render();
        cam.targetTexture = prev;
        RenderTexture.active = rt;
        tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
        RenderTexture.active = null;
        File.WriteAllBytes(Path.Combine(dir, $"f{frame:D5}.png"), tex.EncodeToPNG());
        float t = frame / (float)fps;
        csv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1:F4},{2},{3},{4:F4},{5:F4}", frame, t,
            input.Axis, input.Jump ? 1 : 0, player.position.x, player.position.y - 0.5f));
        if (shotIdx < shotTimes.Length && t >= shotTimes[shotIdx])
        {
            File.WriteAllText(Path.Combine(signal, $"playshot_{shotIdx}.txt"), t.ToString(CultureInfo.InvariantCulture));
            shotIdx++;
        }
        frame++;
        if (t >= duration)
        {
            File.WriteAllText(Path.Combine(dir, "frames.csv"), csv.ToString());
            Time.captureFramerate = 0;
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#endif
            enabled = false;
        }
    }
}
