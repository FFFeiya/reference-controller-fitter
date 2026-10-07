// Demo helper: feeds a scripted input timeline (events.txt: "t axis jump" per line) into the
// controller through its public SetInput API, exactly like held keys would. Not needed in a game.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEngine;

[DefaultExecutionOrder(-100)]
public class RcfDemoInput : MonoBehaviour
{
    struct Ev { public float t; public int axis; public bool jump; }
    readonly List<Ev> events = new List<Ev>();
    int next;
    float t0 = -1f;
    public int Axis { get; private set; }
    public bool Jump { get; private set; }
    Component controller;
    System.Reflection.MethodInfo setInput;

    void Awake()
    {
        string stage = Environment.GetEnvironmentVariable("RCF_STAGE");
        foreach (var line in File.ReadAllLines(Path.Combine(stage, "events.txt")))
        {
            var p = line.Split(' ');
            if (p.Length < 3) continue;
            events.Add(new Ev { t = float.Parse(p[0], CultureInfo.InvariantCulture), axis = int.Parse(p[1]), jump = p[2] == "1" });
        }
        controller = GetComponent("FittedPlatformerController");
        controller.GetType().GetField("useExternalInput").SetValue(controller, true);
        setInput = controller.GetType().GetMethod("SetInput");
    }

    void FixedUpdate()
    {
        if (t0 < 0f) t0 = Time.fixedTime;
        float t = Time.fixedTime - t0;
        while (next < events.Count && events[next].t <= t + 1e-4f)
        {
            Axis = events[next].axis; Jump = events[next].jump; next++;
        }
        setInput.Invoke(controller, new object[] { Axis, Jump });
    }
}
