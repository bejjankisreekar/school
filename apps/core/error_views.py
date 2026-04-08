from django.shortcuts import render


def page_not_found(request, exception=None):
    # Note: custom 404 is only used when DEBUG = False.
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)

