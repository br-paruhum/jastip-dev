from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from .models import Post


def post_list(request):
    qs = Post.objects.filter(status=Post.Status.PUBLISHED)
    page = Paginator(qs, 9).get_page(request.GET.get("page"))
    return render(request, "blog/list.html", {"page_obj": page})


def post_detail(request, slug):
    post = get_object_or_404(Post, slug=slug, status=Post.Status.PUBLISHED)
    related = Post.objects.filter(status=Post.Status.PUBLISHED).exclude(pk=post.pk)[:3]
    return render(request, "blog/detail.html", {"post": post, "related": related})
