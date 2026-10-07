// Demo helper: smooth side-scroller camera.
using UnityEngine;

public class RcfCameraFollow : MonoBehaviour
{
    public Transform target;
    public float smooth = 6f;
    public Vector2 offset = new Vector2(3f, 2.5f);

    void Start()
    {
        if (target == null) target = GameObject.Find("Player").transform;
        transform.position = new Vector3(target.position.x + offset.x, target.position.y + offset.y, -10f);
    }

    void LateUpdate()
    {
        Vector3 goal = new Vector3(target.position.x + offset.x, Mathf.Max(target.position.y, 0f) * 0.6f + offset.y, -10f);
        transform.position = Vector3.Lerp(transform.position, goal, 1f - Mathf.Exp(-smooth * Time.deltaTime));
    }
}
