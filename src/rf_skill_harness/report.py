"""Edit this result-to-UCI mapping alongside your processor before exporting."""


def signal_reports(measurements, metadata, context):
    """Return Signal entries for UCI SignalReport.MessageData.Signal.

    Return [] when there is no reportable detection. Each entry must be a dict
    following the deployed UCI schema; the native host adds report identity,
    security markings and headers. Do not turn voltage statistics into detections
    without defining the decision rule. Example after implementing that rule:

        if measurements['detected']:
            return [{'Parametrics': {'Frequency': {
                'FrequencyAverage': measurements['frequency_hz']}}}]

    Only receiver I/Q-derived measurements and acquisition context belong here.
    """
    return []
