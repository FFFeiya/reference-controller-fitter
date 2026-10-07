// Editor-only harness used by tools/unity_batch_check.py (runs in Unity batch mode).
// Builds a tiny scene (flat ground + player), drives FittedPlatformerController with a
// scripted input sequence through Physics2D.Simulate and writes the feet trajectory to
// the file named by the RCF_OUT environment variable.
using System.Globalization;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class RcfBatchTest
{
    public static void Run()
    {
        string outPath = System.Environment.GetEnvironmentVariable("RCF_OUT");
        var sb = new StringBuilder();
        try
        {
            Physics2D.simulationMode = SimulationMode2D.Script;
            var ground = new GameObject("Ground");
            var gcol = ground.AddComponent<BoxCollider2D>();
            gcol.size = new Vector2(400f, 1f);
            ground.transform.position = new Vector3(0f, -0.5f, 0f);   // top surface at y = 0

            var player = new GameObject("Player");
            var col = player.AddComponent<BoxCollider2D>();
            col.size = new Vector2(0.8f, 1f);
            float half = 0.5f, skin = 0.01f;
            player.transform.position = new Vector3(0f, half + skin, 0f);
            var ctrl = player.AddComponent<FittedPlatformerController>();
            ctrl.useExternalInput = true;
            ctrl.skinWidth = skin;
            ctrl.Initialize();
            var rb = player.GetComponent<Rigidbody2D>();
            Physics2D.SyncTransforms();

            float dt = 0.02f;
            int steps = 150;
            for (int k = 0; k < steps; k++)
            {
                int axis = (k >= 10 && k < 70) ? 1 : 0;
                bool jump = (k >= 25 && k < 45) || (k >= 100 && k < 104);
                ctrl.SetInput(axis, jump);
                ctrl.Step(dt);
                Physics2D.Simulate(dt);
                float feet = rb.position.y - half - skin;
                sb.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1:R},{2:R},{3}",
                    (k + 1) * dt, rb.position.x, feet, ctrl.IsGrounded ? 1 : 0));
            }
            File.WriteAllText(outPath, sb.ToString());
            Debug.Log("RCF_BATCH_TEST_OK " + outPath);
        }
        catch (System.Exception e)
        {
            Debug.LogError("RCF_BATCH_TEST_FAILED " + e);
            EditorApplication.Exit(2);
        }
    }
}
