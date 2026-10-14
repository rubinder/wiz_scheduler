import { useState } from "react";
import { createPortal } from "react-dom";

interface FeedbackFormProps {
  onClose: () => void;
}

export default function FeedbackForm({ onClose }: FeedbackFormProps) {
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [type, setType] = useState<"bug" | "feature" | "feedback" | "other">(
    "feedback"
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const token = localStorage.getItem("access_token");
      const response = await fetch("/api/v1/feedback/submit", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ subject, message, type }),
      });

      if (!response.ok) {
        const err = await response.json();
        const detail = err.detail;
        const errorMsg =
          typeof detail === "object" ? detail.message : detail || "Failed to submit feedback";
        throw new Error(errorMsg);
      }

      setSuccess(true);
      setTimeout(() => {
        onClose();
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  // Portal to <body>: rendered in place, the TopBar's backdrop-blur makes it
  // the containing block for this fixed overlay and the page paints over it.
  if (success) {
    return createPortal(
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
          <h2 className="text-xl font-semibold mb-2 text-green-600">
            ✓ Thank you!
          </h2>
          <p className="text-gray-600">
            Your feedback has been submitted. We'll review it shortly.
          </p>
        </div>
      </div>,
      document.body
    );
  }

  return createPortal(
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 max-w-2xl w-full mx-4 shadow-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-2xl font-semibold">Send Feedback</h2>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-2">
              Feedback Type
            </label>
            <select
              value={type}
              onChange={(e) =>
                setType(
                  e.target.value as "bug" | "feature" | "feedback" | "other"
                )
              }
              className="w-full border rounded px-3 py-2 text-sm"
            >
              <option value="feedback">General Feedback</option>
              <option value="feature">Feature Request</option>
              <option value="bug">Bug Report</option>
              <option value="other">Other</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Subject</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Brief subject"
              required
              minLength={5}
              maxLength={200}
              className="w-full border rounded px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Message</label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Tell us what you think..."
              required
              minLength={10}
              maxLength={5000}
              rows={6}
              className="w-full border rounded px-3 py-2 text-sm resize-none"
            />
          </div>

          {error && <div className="text-sm text-red-600">{error}</div>}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={loading || !subject || !message}
              className="flex-1 bg-blue-600 text-white rounded py-2 font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Submitting..." : "Submit Feedback"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="flex-1 border rounded py-2 font-medium hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
