// Drives a real Unity Editor through the "import the exported controller" workflow for the
// project video. Each step uses normal editor APIs (same effect as the manual clicks); then
// the director writes ready_<n>.txt into RCF_SIGNAL and waits for shot_<n>.txt from the
// external recorder (which captures the editor window). Progress survives domain reloads
// (script import, entering/leaving play mode) through SessionState.
using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

[InitializeOnLoad]
public static class RcfVideoDirector
{
    const string KActive = "rcf_video_active", KStep = "rcf_video_step", KWait = "rcf_video_wait";
    static double waitUntil;
    static string signal, stage;
    static readonly List<Action> steps = new List<Action>();
    static readonly List<string> names = new List<string>();

    static RcfVideoDirector()
    {
        if (!SessionState.GetBool(KActive, false)) return;
        Setup();
        waitUntil = EditorApplication.timeSinceStartup + 2.0;
        EditorApplication.update += Tick;
    }

    public static void Begin()
    {
        SessionState.SetBool(KActive, true);
        SessionState.SetInt(KStep, -1);
        SessionState.SetBool(KWait, false);
        Setup();
        PlayerSettings.runInBackground = true;
        waitUntil = EditorApplication.timeSinceStartup + 4.0;
        EditorApplication.update += Tick;
    }

    static void Setup()
    {
        signal = Environment.GetEnvironmentVariable("RCF_SIGNAL");
        stage = Environment.GetEnvironmentVariable("RCF_STAGE");
        Directory.CreateDirectory(signal);
        steps.Clear(); names.Clear();
        Add("empty", StepEmpty);
        Add("imported", StepImport);
        Add("level", StepLevel);
        Add("player", StepPlayer);
        Add("json", StepJson);
        Add("setup", StepSetup);
        Add("play", StepPlay);
    }

    static void Add(string name, Action a) { names.Add(name); steps.Add(a); }

    static double lastBeat;

    static void Tick()
    {
        double now = EditorApplication.timeSinceStartup;
        if (now - lastBeat > 1.0)
        {
            lastBeat = now;
            File.WriteAllText(Path.Combine(signal, "heartbeat.txt"),
                $"{SessionState.GetInt(KStep, -1)} {now:F1} playing={EditorApplication.isPlaying}");
        }
        if (now < waitUntil) return;
        if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
        int step = SessionState.GetInt(KStep, -1);
        // announce the finished step (written from the update loop: delayCall needs repaints,
        // which a background editor may not do)
        string pending = SessionState.GetString("rcf_video_pending", "");
        if (pending != "")
        {
            RepaintAll();
            SessionState.SetString("rcf_video_pending", "");
            File.WriteAllText(Path.Combine(signal, $"ready_{step}.txt"), pending);
            waitUntil = now + 0.5;
            return;
        }
        if (SessionState.GetBool(KWait, false))
        {
            if (!File.Exists(Path.Combine(signal, $"shot_{step}.txt"))) return;
            SessionState.SetBool(KWait, false);
        }
        step++;
        SessionState.SetInt(KStep, step);
        if (step >= steps.Count)
        {
            EditorApplication.update -= Tick;
            SessionState.SetBool(KActive, false);
            File.WriteAllText(Path.Combine(signal, "finished.txt"), "ok");
            EditorApplication.Exit(0);
            return;
        }
        try { steps[step](); }
        catch (Exception e)
        {
            File.WriteAllText(Path.Combine(signal, "error.txt"), names[step] + "\n" + e);
            SessionState.SetBool(KActive, false);
            EditorApplication.Exit(3);
            return;
        }
        bool isPlayStep = names[step] == "play";
        SessionState.SetBool(KWait, !isPlayStep);
        if (!isPlayStep) SessionState.SetString("rcf_video_pending", names[step]);
        // let the editor finish importing/compiling and repaint before the screenshot
        waitUntil = EditorApplication.timeSinceStartup + 2.5;
        RepaintAll();
    }

    static void ShowScene()
    {
        EditorWindow.FocusWindowIfItsOpen(typeof(SceneView));
    }

    static void ShowGame()
    {
        var gv = Type.GetType("UnityEditor.GameView,UnityEditor");
        if (gv != null) EditorWindow.FocusWindowIfItsOpen(gv);
    }

    /// Collapse every component except the fitted controller so its parameters are visible.
    static void CollapseAllBut(GameObject go, string keep)
    {
        foreach (var c in go.GetComponents<Component>())
        {
            if (c == null) continue;
            UnityEditorInternal.InternalEditorUtility.SetIsInspectorExpanded(c, c.GetType().Name == keep);
        }
        ActiveEditorTracker.sharedTracker.ForceRebuild();
    }

    static void RepaintAll()
    {
        foreach (var w in Resources.FindObjectsOfTypeAll<EditorWindow>()) w.Repaint();
        SceneView.RepaintAll();
    }

    // ---------------------------------------------------------------------------------------
    static void StepEmpty()
    {
        EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
        var cam = Camera.main;
        cam.orthographic = true;
        cam.orthographicSize = 6f;
        cam.backgroundColor = new Color(0.12f, 0.13f, 0.17f);
        cam.clearFlags = CameraClearFlags.SolidColor;
        var light = GameObject.Find("Directional Light");
        if (light != null) UnityEngine.Object.DestroyImmediate(light);
        EditorSettings.defaultBehaviorMode = EditorBehaviorMode.Mode2D;
        var sv = SceneView.lastActiveSceneView;
        if (sv != null) sv.in2DMode = true;
        EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), "Assets/Demo.unity");
    }

    static void StepImport()
    {
        // equivalent of dragging the two exported files into the Project window
        Directory.CreateDirectory("Assets/RCF");
        File.Copy(Path.Combine(stage, "FittedPlatformerController.cs"), "Assets/RCF/FittedPlatformerController.cs", true);
        File.Copy(Path.Combine(stage, "fitted_controller.json"), "Assets/RCF/fitted_controller.json", true);
        // a white square sprite (1 unit) for the level and the player
        var tex = new Texture2D(32, 32);
        var px = new Color[32 * 32];
        for (int i = 0; i < px.Length; i++) px[i] = Color.white;
        tex.SetPixels(px); tex.Apply();
        File.WriteAllBytes("Assets/RCF/Square.png", tex.EncodeToPNG());
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        var ti = (TextureImporter)AssetImporter.GetAtPath("Assets/RCF/Square.png");
        ti.textureType = TextureImporterType.Sprite;
        ti.spritePixelsPerUnit = 32;
        ti.filterMode = FilterMode.Point;
        ti.SaveAndReimport();
        var script = AssetDatabase.LoadAssetAtPath<MonoScript>("Assets/RCF/FittedPlatformerController.cs");
        Selection.activeObject = script;
        EditorGUIUtility.PingObject(script);
    }

    static Sprite Square => AssetDatabase.LoadAssetAtPath<Sprite>("Assets/RCF/Square.png");

    static GameObject Block(string name, float x0, float y0, float x1, float y1, Color c, Transform parent)
    {
        var go = new GameObject(name);
        go.transform.parent = parent;
        var sr = go.AddComponent<SpriteRenderer>();
        sr.sprite = Square;
        sr.color = c;
        go.transform.position = new Vector3((x0 + x1) / 2f, (y0 + y1) / 2f, 0f);
        go.transform.localScale = new Vector3(x1 - x0, y1 - y0, 1f);
        go.AddComponent<BoxCollider2D>();
        return go;
    }

    static void StepLevel()
    {
        // level layout shared with the Godot demo (level.txt: "name|x0|y0|x1|y1", units, y up)
        var level = new GameObject("Level").transform;
        var col = new Color(0.30f, 0.34f, 0.44f);
        foreach (var line in File.ReadAllLines(Path.Combine(stage, "level.txt")))
        {
            var p = line.Split('|');
            if (p.Length < 5) continue;
            Func<int, float> f = i => float.Parse(p[i], System.Globalization.CultureInfo.InvariantCulture);
            Block(p[0], f(1), f(2), f(3), f(4), col, level);
        }
        Selection.activeGameObject = GameObject.Find("Step 2");
        ShowScene();
        var sv = SceneView.lastActiveSceneView;
        if (sv != null) { sv.in2DMode = true; sv.Frame(new Bounds(new Vector3(14, 2, 0), new Vector3(40, 16, 1)), true); }
        EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
    }

    static void StepPlayer()
    {
        var player = new GameObject("Player");
        var sr = player.AddComponent<SpriteRenderer>();
        sr.sprite = Square;
        sr.color = Color.white;
        sr.sortingOrder = 10;
        player.transform.position = new Vector3(0f, 0.5f + 0.01f, 0f);
        player.transform.localScale = new Vector3(0.8f, 1f, 1f);
        player.AddComponent<BoxCollider2D>();
        // [RequireComponent] adds the Rigidbody2D; the controller makes it Kinematic at Awake
        Undo.AddComponent(player, Type.GetType("FittedPlatformerController, Assembly-CSharp"));
        CollapseAllBut(player, "FittedPlatformerController");
        Selection.activeGameObject = player;
        ShowScene();
        var sv = SceneView.lastActiveSceneView;
        if (sv != null) sv.Frame(new Bounds(new Vector3(5, 2, 0), new Vector3(20, 9, 1)), true);
        EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
    }

    static void StepJson()
    {
        var player = GameObject.Find("Player");
        var comp = player.GetComponent(Type.GetType("FittedPlatformerController, Assembly-CSharp"));
        var so = new SerializedObject(comp);
        so.FindProperty("fittedParamsJson").objectReferenceValue =
            AssetDatabase.LoadAssetAtPath<TextAsset>("Assets/RCF/fitted_controller.json");
        so.ApplyModifiedProperties();
        CollapseAllBut(player, "FittedPlatformerController");
        Selection.activeGameObject = player;
        ShowScene();
        EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
    }

    static void StepSetup()
    {
        // demo helpers only (scripted keyboard input, camera follow, frame recorder) – not needed in a real game
        Directory.CreateDirectory("Assets/RCFDemo");
        foreach (var f in new[] { "RcfDemoInput.cs", "RcfCameraFollow.cs", "RcfFrameRecorder.cs" })
            File.Copy(Path.Combine(stage, "unity_runtime", f), "Assets/RCFDemo/" + f, true);
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Selection.activeGameObject = GameObject.Find("Player");
    }

    static void StepPlay()
    {
        var player = GameObject.Find("Player");
        var input = Type.GetType("RcfDemoInput, Assembly-CSharp");
        var follow = Type.GetType("RcfCameraFollow, Assembly-CSharp");
        var rec = Type.GetType("RcfFrameRecorder, Assembly-CSharp");
        if (player.GetComponent(input) == null) player.AddComponent(input);
        var cam = Camera.main.gameObject;
        if (cam.GetComponent(follow) == null) cam.AddComponent(follow);
        if (cam.GetComponent(rec) == null) cam.AddComponent(rec);
        CollapseAllBut(player, "FittedPlatformerController");
        EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
        Selection.activeGameObject = player;
        ShowGame();
        EditorApplication.isPlaying = true;
    }
}
