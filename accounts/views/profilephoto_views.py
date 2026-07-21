from rest_framework import status
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff

ALLOWED_CONTENT_TYPES = ['image/jpeg', 'image/png', 'image/webp']
MAX_PHOTO_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


class ProfilePhotoView(APIView):
    permission_classes = [IsAdmin | IsStaff]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        photo = request.FILES.get('photo')
        if not photo:
            return Response({'error': "'photo' file is required."}, status=status.HTTP_400_BAD_REQUEST)

        if photo.content_type not in ALLOWED_CONTENT_TYPES:
            return Response(
                {'error': 'Only JPEG, PNG, or WEBP images are allowed.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if photo.size > MAX_PHOTO_SIZE_BYTES:
            return Response({'error': 'Image must be smaller than 5MB.'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if user.profile_photo:
            user.profile_photo.delete(save=False)  # remove old file before saving new one
        user.profile_photo = photo
        user.save(update_fields=['profile_photo'])

        return Response(
            {'message': 'Profile photo updated.', 'profile_photo': user.profile_photo.url},
            status=status.HTTP_200_OK
        )

    def delete(self, request):
        user = request.user
        if user.profile_photo:
            user.profile_photo.delete(save=False)
            user.profile_photo = None
            user.save(update_fields=['profile_photo'])
        return Response({'message': 'Profile photo removed.'}, status=status.HTTP_200_OK)
