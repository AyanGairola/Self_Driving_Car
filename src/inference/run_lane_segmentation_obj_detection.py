import numpy as np
import os
import cv2

def smooth_line(new_line, old_line, alpha=0.8):
    """
    Smooths lane line coordinates by blending new detection with the previous one.
    If new_line is missing, returns old_line (unless we are doing the fade-out logic separately).
    alpha controls how much weight is given to the new detection:
      alpha = 1.0 -> fully new detection
      alpha = 0.0 -> fully old detection
    """
    if new_line is None:
        return old_line
    if old_line is None:
        return new_line
    # Each line is ((x1, y1), (x2, y2))
    smoothed_start = (
        int(alpha * new_line[0][0] + (1 - alpha) * old_line[0][0]),
        int(alpha * new_line[0][1] + (1 - alpha) * old_line[0][1])
    )
    smoothed_end = (
        int(alpha * new_line[1][0] + (1 - alpha) * old_line[1][0]),
        int(alpha * new_line[1][1] + (1 - alpha) * old_line[1][1])
    )
    return (smoothed_start, smoothed_end)

class LaneDetector:
    def __init__(
        self,
        kernel_size=5,
        low_threshold=50,
        high_threshold=150,
        left_slope_range=(-2.5, -0.3),
        right_slope_range=(0.3, 2.5),
        hough_threshold=15,
        min_line_length=10,
        max_line_gap=300,
        apply_white_mask=True,
        right_white_threshold=2000
    ):
        """
        :param right_white_threshold: If there's not enough white in the right half,
                                      the right lane is discarded for this frame.
        """
        self.kernel_size = kernel_size
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
        self.left_slope_range = left_slope_range
        self.right_slope_range = right_slope_range
        self.hough_threshold = hough_threshold
        self.min_line_length = min_line_length
        self.max_line_gap = max_line_gap
        self.apply_white_mask = apply_white_mask
        self.right_white_threshold = right_white_threshold
        self.last_detected_lines = (None, None)  # (left_line, right_line)

    def region_selection(self, image):
        height, width = image.shape
        mask = np.zeros_like(image)
        polygon = np.array([[
            (0, height),
            (width // 2, int(height * 0.6)),
            (width, height)
        ]], np.int32)
        cv2.fillPoly(mask, polygon, 255)
        return cv2.bitwise_and(image, mask)

    def hough_transform(self, image):
        return cv2.HoughLinesP(
            image,
            rho=1,
            theta=np.pi/180,
            threshold=self.hough_threshold,
            minLineLength=self.min_line_length,
            maxLineGap=self.max_line_gap
        )

    def average_slope_intercept(self, lines, width):
        left_lines, left_weights = [], []
        right_lines, right_weights = [], []

        (left_min, left_max) = self.left_slope_range
        (right_min, right_max) = self.right_slope_range
        mid_x = width // 2

        for line in lines:
            for x1, y1, x2, y2 in line:
                if x1 == x2:
                    continue  # skip vertical lines
                slope = (y2 - y1) / (x2 - x1)
                intercept = y1 - slope * x1
                length = np.sqrt((y2 - y1)**2 + (x2 - x1)**2)
                mx = (x1 + x2) / 2

                if slope < 0:
                    # left lane
                    if left_min <= slope <= left_max and mx < mid_x:
                        left_lines.append((slope, intercept))
                        left_weights.append(length)
                else:
                    # right lane
                    if right_min <= slope <= right_max and mx > mid_x:
                        right_lines.append((slope, intercept))
                        right_weights.append(length)

        left_lane = None
        right_lane = None
        if len(left_weights) > 0:
            left_lane = np.dot(left_weights, left_lines) / np.sum(left_weights)
        if len(right_weights) > 0:
            right_lane = np.dot(right_weights, right_lines) / np.sum(right_weights)

        return left_lane, right_lane

    def pixel_points(self, y1, y2, line):
        if line is None:
            return None
        slope, intercept = line
        if abs(slope) < 1e-6:
            return None
        x1 = int((y1 - intercept) / slope)
        x2 = int((y2 - intercept) / slope)
        return (x1, int(y1)), (x2, int(y2))

    def lane_lines(self, image, lines):
        height, width = image.shape[:2]
        left_lane, right_lane = self.average_slope_intercept(lines, width)
        y1 = height
        y2 = int(height * 0.6)
        left_line = self.pixel_points(y1, y2, left_lane)
        right_line = self.pixel_points(y1, y2, right_lane)
        return left_line, right_line

    def draw_lane_lines(self, image, lines, color=[255, 0, 0], thickness=12):
        line_image = np.zeros_like(image)
        for line in lines:
            if line is not None:
                cv2.line(line_image, *line, color, thickness)
        return cv2.addWeighted(image, 1.0, line_image, 1.0, 0.0)

    def process_image(self, image):
        grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(grayscale, (self.kernel_size, self.kernel_size), 0)
        edges = cv2.Canny(blur, self.low_threshold, self.high_threshold)

        if self.apply_white_mask:
            hls = cv2.cvtColor(image, cv2.COLOR_BGR2HLS)
            lower_white = np.array([0, 200, 0])
            upper_white = np.array([255, 255, 255])
            white_mask = cv2.inRange(hls, lower_white, upper_white)
            edges = cv2.bitwise_and(edges, white_mask)

        region = self.region_selection(edges)
        lines = self.hough_transform(region)
        if lines is None:
            self.last_detected_lines = (None, None)
            return image.copy()

        left_line, right_line = self.lane_lines(image, lines)

        # If there's not enough white on the right side, discard right lane
        if self.apply_white_mask:
            height, width = edges.shape
            # We assume 'white_mask' was created above
            right_half_mask = white_mask[:, width//2:]
            if right_half_mask is not None:
                right_count = cv2.countNonZero(right_half_mask)
                if right_count < self.right_white_threshold:
                    right_line = None

        self.last_detected_lines = (left_line, right_line)
        return self.draw_lane_lines(image, (left_line, right_line))

def display_images(input_folder, display_time=20):
    """
    Enhanced display loop with a 'timeout' mechanism for the right lane.
    If the right lane is missing for more than RIGHT_LANE_TIMEOUT_FRAMES in a row,
    we discard it completely (so it won't remain static).
    """
    RIGHT_LANE_TIMEOUT_FRAMES = 5  # If missing 5 frames in a row, drop it
    consecutive_right_missing = 0

    image_files = [img for img in os.listdir(input_folder) if img.endswith(".jpg") or img.endswith(".png")]
    image_files.sort(key=lambda x: int(x.split('.')[0]))

    lane_detector = LaneDetector(apply_white_mask=True, right_white_threshold=2000)

    # We'll store smoothed lines for each lane
    smoothed_left = None
    smoothed_right = None
    smoothing_alpha = 0.8

    for image_file in image_files:
        image_path = os.path.join(input_folder, image_file)
        image = cv2.imread(image_path)
        if image is None:
            print(f"Failed to load {image_path}")
            continue

        processed_image = lane_detector.process_image(image)
        left_line, right_line = lane_detector.last_detected_lines

        # Smooth the left lane
        smoothed_left = smooth_line(left_line, smoothed_left, alpha=smoothing_alpha)

        # For the right lane, check if it's missing
        if right_line is None:
            consecutive_right_missing += 1
        else:
            consecutive_right_missing = 0

        # If missing too long, drop it
        if consecutive_right_missing > RIGHT_LANE_TIMEOUT_FRAMES:
            smoothed_right = None
        else:
            smoothed_right = smooth_line(right_line, smoothed_right, alpha=smoothing_alpha)

        final_image = lane_detector.draw_lane_lines(processed_image.copy(), (smoothed_left, smoothed_right))

        cv2.imshow('Lane Detection', final_image)
        print(f"Displaying {image_file}")
        if cv2.waitKey(display_time) == ord('q'):
            break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    input_folder = "../../data/driving_dataset"
    display_images(input_folder, display_time=10)
