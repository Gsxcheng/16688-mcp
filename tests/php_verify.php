<?php
// Server-side check written from the official docs' PHP sample: decode the JSON body, drop sign, re-sign.
function normalize_sign_value($value) {
    if (!is_array($value)) return $value;
    if (array_is_list($value)) return array_map('normalize_sign_value', $value);
    ksort($value);
    foreach ($value as $k => $item) $value[$k] = normalize_sign_value($item);
    return $value;
}
function canonical_value($value) {
    if (is_bool($value)) return $value ? 'true' : 'false';
    if (is_array($value)) {
        $encoded = json_encode(normalize_sign_value($value), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        return $encoded === false ? '' : $encoded;
    }
    if ($value === null) return '';
    return (string) $value;
}
[$_, $path, $secret] = $argv;
$params = json_decode(file_get_contents('php://stdin'), true);
$given = $params['sign'];
$parts_path = explode('/', trim($path, '/'));
$controller = $parts_path[1];
$action = $parts_path[2];
$method = 'POST';
unset($params['sign']);
ksort($params);
$parts = [];
foreach ($params as $k => $v) {
    $parts[] = $k . '=' . canonical_value($v);
}
$plain = strtoupper($method) . "\n" . strtolower($controller) . "\n" . strtolower($action) . "\n" . implode('&', $parts);
$sign = hash_hmac('sha256', $plain, $secret);
echo $sign === $given ? "OK" : "MISMATCH\n$plain";
